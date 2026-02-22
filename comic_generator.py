import os
import json
import traceback
import threading
import time
import random
import requests
from pathlib import Path
from flask import current_app
from google import genai
from google.genai import types
import workflow_db_manager
import workflow_cache_manager
import workflow_model_config

class ComicGenerator:
    CENSORED_IMAGE_URL = "https://upload.wikimedia.org/wikipedia/commons/thumb/7/70/Censored_rubber_stamp.svg/960px-Censored_rubber_stamp.svg.png"

    def __init__(self):
        self.client = None
        self._init_client()

    def _init_client(self):
        try:
            gcp_creds_raw = os.getenv("GCP_CREDENTIALS")
            # Пытаемся найти Project ID всеми возможными способами
            project_id = os.getenv("GCP_PROJECT_ID") or os.getenv("GCP_PROJECT") or os.getenv("GOOGLE_CLOUD_PROJECT")
            
            if gcp_creds_raw:
                import base64
                import tempfile
                
                try:
                    creds_info = None
                    try:
                        decoded = base64.b64decode(gcp_creds_raw).decode("utf-8")
                        creds_info = json.loads(decoded)
                    except Exception:
                        creds_info = json.loads(gcp_creds_raw)
                    
                    if creds_info:
                        if not project_id:
                            project_id = (
                                creds_info.get("project_id") or 
                                creds_info.get("quota_project_id") or 
                                creds_info.get("project")
                            )

                        # Создаем временный файл для SDK
                        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as tf:
                            json.dump(creds_info, tf)
                            os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = tf.name
                            print(f"[ComicGenerator] Credentials written to temp file and GOOGLE_APPLICATION_CREDENTIALS set.")
                except Exception as e:
                    print(f"[ComicGenerator] Error setting up credentials: {e}")

            if project_id:
                # В новой библиотеке genai для Vertex AI используется vertexai=True
                self.client = genai.Client(
                    vertexai=True,
                    project=project_id,
                    location="us-central1"
                )
                print(f"[ComicGenerator] Client initialized for project {project_id}")
            else:
                print("[ComicGenerator] No project_id found (checked env and JSON), client not initialized.")
        except Exception as e:
            print(f"[ComicGenerator] Error initializing GenAI Client: {e}")
            traceback.print_exc()

    def generate_image(self, prompt_text, book_id, section_id, max_retries=2):
        """Генерирует изображение и возвращает бинарные данные с поддержкой ретраев."""
        if not self.client:
            return None, "GenAI Client not initialized"

        model_name = workflow_model_config.get_model_for_operation('generate_comic', 'primary') or "gemini-2.0-flash-exp"
        
        for attempt in range(max_retries + 1):
            try:
                if attempt > 0:
                    wait_time = 5 * attempt
                    print(f"[ComicGenerator] Retry attempt {attempt} for {section_id}, waiting {wait_time}s...")
                    time.sleep(wait_time)

                print(f"[ComicGenerator] Generating image for {book_id}/{section_id} using {model_name} (Attempt {attempt+1})...")
                
                response = self.client.models.generate_content(
                    model=model_name,
                    contents=prompt_text,
                    config=types.GenerateContentConfig(
                        response_modalities=["IMAGE"]
                    )
                )
                
                image_data = None
                
                # Безопасная проверка структуры ответа
                if response and response.candidates and len(response.candidates) > 0:
                    candidate = response.candidates[0]
                    if candidate.content and candidate.content.parts:
                        for part in candidate.content.parts:
                            if part.inline_data:
                                image_data = part.inline_data.data
                                break
                
                # Резервный поиск в частях самого ответа
                if not image_data and hasattr(response, 'parts') and response.parts:
                    for part in response.parts:
                        if part.inline_data:
                            image_data = part.inline_data.data
                            break

                if image_data:
                    print(f"[ComicGenerator] Image generated for {section_id} (Size: {len(image_data)} bytes)")
                    return image_data, None
                else:
                    # Если картинки нет, возможно, сработал фильтр безопасности
                    finish_reason = "Unknown"
                    if response.candidates and response.candidates[0].finish_reason:
                        finish_reason = str(response.candidates[0].finish_reason)
                    
                    error_msg = f"No image in response. Finish reason: {finish_reason}"
                    print(f"[ComicGenerator] {error_msg}")
                    # Не ретраим при ошибке контента (обычно это фильтры)
                    return None, error_msg

            except Exception as e:
                error_str = str(e)
                if "429" in error_str or "RESOURCE_EXHAUSTED" in error_str:
                    print(f"[ComicGenerator] Rate limit hit for {section_id}. Attempt {attempt+1}/{max_retries+1}")
                    if attempt == max_retries:
                        return None, f"Rate limit exhausted after {max_retries+1} attempts"
                    continue # Переход к следующей попытке (ретрай)
                
                error_msg = f"Error calling Gemini: {e}"
                print(f"[ComicGenerator] {error_msg}")
                # traceback.print_exc() # Закомментировано, чтобы не спамить лог
                return None, error_msg

        return None, "Unexpected end of generate_image loop"

    def process_book_comic(self, book_id, app_instance):
        """Цикл генерации комикса по всем секциям книги с сохранением в БД."""
        with app_instance.app_context():
            print(f"[ComicGenerator] Starting comic generation for book {book_id}")
            
            sections = workflow_db_manager.get_sections_for_book_workflow(book_id)
            if not sections:
                print(f"[ComicGenerator] No sections found for book {book_id}")
                return

            for section in sections:
                section_id = section['section_id']
                
                # Проверяем, не сгенерировано ли уже (чтобы можно было догенерировать при ошибках)
                if workflow_db_manager.get_comic_image_workflow(section_id):
                    print(f"[ComicGenerator] Section {section_id} already has an image. Skipping.")
                    continue

                summary = workflow_cache_manager.load_section_stage_result(book_id, section_id, 'summarize')
                
                if not summary or not summary.strip() or len(summary.strip()) < 50:
                    print(f"[ComicGenerator] Summary too short or empty for section {section_id}. skipping.")
                    continue

                # Если поймали IMAGE_SAFETY, пробуем перефразировать или упростить
                retry_with_variation = False
                
                for attempt in range(2): # 0: original, 1: simplified variation
                    if attempt == 0:
                        prompt = f"Нарисуй иллюстрацию к тексту в виде комикса из нескольких плиток: {summary}"
                    else:
                        print(f"[ComicGenerator] Retrying with simplified prompt for section {section_id} due to safety filter...")
                        # Используем полный текст суммаризации, но добавляем инструкции по безопасности на английском
                        prompt = f"Comic book illustration, cinematic style, safe for all ages: {summary}"

                    image_data, error = self.generate_image(prompt, book_id, section_id)
                    
                    if image_data:
                        workflow_db_manager.save_comic_image_workflow(book_id, section_id, image_data)
                        print(f"[ComicGenerator] Successfully saved comic to DB for section {section_id}")
                        break
                    elif error and "IMAGE_SAFETY" in error:
                        if attempt == 0:
                            print(f"[ComicGenerator] Safety filter hit for section {section_id}. Trying one more time with variation.")
                            continue # Идем на второй круг
                        else:
                            print(f"[ComicGenerator] Safety filter hit again for section {section_id}. Inserting CENSORED placeholder.")
                            self._save_censored_placeholder(book_id, section_id)
                    else:
                        print(f"[ComicGenerator] Permanent failure for section {section_id}: {error}. Inserting CENSORED placeholder.")
                        self._save_censored_placeholder(book_id, section_id)
                        break # Другие ошибки не ретраим
                
                # Обязательная пауза между генерациями для избежания 429 ошибки
                # Увеличено до 30 секунд по просьбе пользователя + небольшой рандом
                time.sleep(30 + random.randint(1, 5))

            print(f"[ComicGenerator] Finished comic generation for book {book_id}")

    def _save_censored_placeholder(self, book_id, section_id):
        """Скачивает и сохраняет заглушку CENSORED в БД."""
        try:
            resp = requests.get(self.CENSORED_IMAGE_URL, timeout=10)
            if resp.status_code == 200:
                workflow_db_manager.save_comic_image_workflow(book_id, section_id, resp.content)
                print(f"[ComicGenerator] Saved CENSORED placeholder for section {section_id}")
        except Exception as e:
            print(f"[ComicGenerator] Failed to download CENSORED placeholder: {e}")

def delete_comic_folder(book_id):
    """Метод оставлен для обратной совместимости."""
    pass
