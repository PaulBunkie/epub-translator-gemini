import os
import json
import traceback
import threading
import time
import random
import requests
import re
import base64
from pathlib import Path
from flask import current_app
import google.generativeai as google_genai
from google import genai as vertex_genai
from google.genai import types
import workflow_db_manager
import workflow_cache_manager
import workflow_model_config
import workflow_translation_module

class ComicGenerator:
    CENSORED_IMAGE_URL = "https://upload.wikimedia.org/wikipedia/commons/thumb/7/70/Censored_rubber_stamp.svg/960px-Censored_rubber_stamp.svg.png"
    OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"
    LITEROUTER_API_URL = "https://api.literouter.com/v1/chat/completions"

    # Единый промпт для всех методов генерации
    BASE_PROMPT = (
        "A sequence of cinematic live-action film frames, modern asymmetric panel layout, visual storytelling through multiple connected scenes, "
        "photorealistic human characters, realistic skin texture, natural anatomy and facial expressions, dramatic cinematic lighting, "
        "shallow depth of field, realistic environments, ultra-detailed movie still aesthetic, grounded realism, high-end sci-fi thriller atmosphere, "
        "dynamic camera angles, authentic film grain, anamorphic lens look, color graded like a modern HBO/Netflix production, emotionally expressive cinematic moments. "
        "No text, no speech bubbles, no captions, no letters, no subtitles, no labels, no numbering, no frame numbers, no panel numbers, no UI elements, "
        "no comic style, no cartoon, no cel shading, no illustration, no exaggerated features, no anime."
    )

    # Промпты инкапсулированы внутри модуля
    VISUAL_ANALYSIS_SYSTEM_PROMPT = """You are an expert in visual character design and literary analysis. Your task is to create a "Visual Bible" for a book based on its summaries.
        
CRITICAL RULES:
1. Identify all recurring or important characters and entities.
2. For each, provide a detailed visual description in ENGLISH.
3. Focus on: age, gender, ethnicity, build, hair style/color, distinctive facial features, typical clothing style, and specific accessories.
4. IMPORTANT: If the text doesn't specify visual details, INFER them logically based on the character's role, personality, and context.
5. STYLE ADHERENCE: All descriptions must be compatible with a "Cinematic Live-Action Film / High-end HBO/Netflix production" style.
6. Return ONLY a JSON object where keys are character names and values are their visual descriptions.

Example Output:
{
  "Jamie": "A tall, athletic woman in her late 20s, short-cropped dark hair, sharp features, wearing tactical outdoor gear with clean lines.",
  "Bella": "A massive, bioluminescent kaiju resembling a cross between a dragon and a deep-sea fish, iridescent scales, glowing teeth."
}"""

    VISUAL_ANALYSIS_USER_TEMPLATE = """Analyze the following book summaries and extract visual profiles for all key characters. If details are missing, invent them logically to fix the character's look for the entire book. Return ONLY valid JSON.

Book Summaries:
{text}"""

    def __init__(self):
        self.client = None
        self._init_client()

    def _init_client(self):
        try:
            gcp_creds_raw = os.getenv("GCP_CREDENTIALS")
            project_id = os.getenv("GCP_PROJECT_ID") or os.getenv("GCP_PROJECT") or os.getenv("GOOGLE_CLOUD_PROJECT")
            
            if gcp_creds_raw:
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

                        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as tf:
                            json.dump(creds_info, tf)
                            os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = tf.name
                            print(f"[ComicGenerator] Credentials written to temp file.")
                except Exception as e:
                    print(f"[ComicGenerator] Error setting up credentials: {e}")

            if project_id:
                self.client = vertex_genai.Client(
                    vertexai=True,
                    project=project_id,
                    location="global"
                )
                print(f"[ComicGenerator] Client initialized for project {project_id} (Location: global)")
            else:
                print("[ComicGenerator] No project_id found, client not initialized.")
            
            # Инициализация Google API Key (для моделей с префиксом models/)
            google_api_key = os.getenv("GOOGLE_API_KEY")
            if google_api_key:
                google_genai.configure(api_key=google_api_key)
                print("[ComicGenerator] Google Generative AI configured with API Key.")
                
        except Exception as e:
            print(f"[ComicGenerator] Error initializing clients: {e}")
            traceback.print_exc()

    def _generate_with_vertex(self, model_name, prompt_text, book_id, section_id, attempt):
        """Метод генерации через Vertex AI SDK."""
        if not self.client:
            return None, "Vertex client not initialized"
        
        # Убираем префиксы если они есть
        actual_model = model_name.replace('vertex/', '').replace('models/', '')
        
        try:
            print(f"[ComicGenerator] [Vertex] Generating image for {section_id} using {actual_model}...")
            response = self.client.models.generate_content(
                model=actual_model,
                contents=prompt_text,
                config=types.GenerateContentConfig(
                    response_modalities=["IMAGE"]
                )
            )
            
            image_data = None
            if response and response.candidates and len(response.candidates) > 0:
                candidate = response.candidates[0]
                if candidate.content and candidate.content.parts:
                    for part in candidate.content.parts:
                        if part.inline_data:
                            image_data = part.inline_data.data
                            break
            
            if image_data:
                return image_data, None
            
            finish_reason = "Unknown"
            if response and response.candidates and response.candidates[0].finish_reason:
                finish_reason = str(response.candidates[0].finish_reason)
            
            return None, f"IMAGE_SAFETY" if "SAFETY" in finish_reason else f"No image: {finish_reason}"
        except Exception as e:
            return None, str(e)

    def _generate_with_openrouter(self, model_name, prompt_text, book_id, section_id, attempt):
        """Метод генерации через OpenRouter API согласно предоставленному примеру."""
        api_key = os.getenv("OPENROUTER_API_KEY")
        if not api_key:
            return None, "OPENROUTER_API_KEY missing"

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        data = {
            "model": model_name,
            "messages": [{"role": "user", "content": prompt_text}],
            "modalities": ["image"]
        }

        try:
            print(f"[ComicGenerator] [OpenRouter] Requesting image for {section_id} using {model_name}...")
            response = requests.post(self.OPENROUTER_API_URL, headers=headers, json=data, timeout=120)
            
            if response.status_code != 200:
                # Ограничиваем лог ошибки, чтобы не выплюнуть случайно бинарщину
                return None, f"OpenRouter error {response.status_code}: {response.text[:200]}"
            
            result = response.json()
            
            if result.get("choices"):
                message = result["choices"][0].get("message", {})
                if message.get("images"):
                    # Берем первое изображение
                    image = message["images"][0]
                    image_url = image.get("image_url", {}).get("url")
                    
                    if image_url:
                        # Логируем только начало
                        print(f"[ComicGenerator] [OpenRouter] Generated image URL: {image_url[:50]}...")
                        
                        if image_url.startswith('data:image'):
                            try:
                                # Обработка base64 data URL
                                header, encoded = image_url.split(",", 1)
                                return base64.b64decode(encoded), None
                            except Exception as be:
                                return None, f"Failed to decode base64: {be}"
                        else:
                            # Обычный URL
                            img_resp = requests.get(image_url, timeout=60)
                            if img_resp.status_code == 200:
                                return img_resp.content, None
                            return None, f"Failed to download image from URL (Status: {img_resp.status_code})"
            
            return None, "No image found in OpenRouter response"
        except Exception as e:
            print(f"[ComicGenerator] [OpenRouter] Exception: {e}")
            return None, str(e)

    def generate_image(self, prompt_text, book_id, section_id, max_retries=2):
        """Генерирует изображение, перебирая уровни fallback из конфига."""
        levels = ['primary', 'fallback_level1', 'fallback_level2']
        
        for level in levels:
            model_name = workflow_model_config.get_model_for_operation('generate_comic', level)
            if not model_name:
                continue

            for attempt in range(max_retries + 1):
                if attempt > 0:
                    wait_time = 10 * attempt
                    print(f"[ComicGenerator] Retry {level} attempt {attempt} for {section_id}, waiting {wait_time}s...")
                    time.sleep(wait_time)

                # Определяем провайдера СТРОГО
                is_vertex = model_name.startswith('vertex/') or model_name.startswith('models/') or 'gemini' in model_name.lower()
                
                if is_vertex:
                    image_data, error = self._generate_with_vertex(model_name, prompt_text, book_id, section_id, attempt)
                else:
                    image_data, error = self._generate_with_openrouter(model_name, prompt_text, book_id, section_id, attempt)

                if image_data:
                    return image_data, None
                
                print(f"[ComicGenerator] Level {level} failed: {error}")
                
                # Если ошибка безопасности, переходим к следующему уровню (модели)
                if "IMAGE_SAFETY" in str(error).upper():
                    break 
                
                # Если не лимиты, переходим к следующей модели
                if "429" not in str(error) and "RESOURCE_EXHAUSTED" not in str(error):
                    break

        return None, "All models and retries exhausted"

    def _run_visual_analysis(self, book_id, sections):
        """Выполняет визуальный анализ книги используя систему фоллбэков."""
        print(f"[ComicGenerator] Running visual analysis for book {book_id}...")
        
        all_summaries = []
        for sec in sections:
            summary = workflow_cache_manager.load_section_stage_result(book_id, sec['section_id'], 'summarize')
            if summary:
                all_summaries.append(f"Глава {sec['order_in_book'] + 1}: {summary}")
        
        full_text = "\n\n".join(all_summaries)
        if not full_text:
            return None

        levels = ['primary', 'fallback_level1', 'fallback_level2']
        full_prompt = f"{self.VISUAL_ANALYSIS_SYSTEM_PROMPT}\n\n{self.VISUAL_ANALYSIS_USER_TEMPLATE.format(text=full_text)}"

        for level in levels:
            model_name = workflow_model_config.get_model_for_operation('visual_analysis', level)
            if not model_name:
                continue

            try:
                print(f"[ComicGenerator] [Analysis] Trying level {level}: {model_name}...")
                
                result = None
                # СТРОГОЕ определение провайдера (согласно workflow_translation_module)
                is_vertex = model_name.startswith('vertex/')
                is_google = model_name.startswith('models/')
                is_literouter = model_name.startswith('literouter/')
                
                if is_vertex or is_google:
                    if is_vertex and not self.client:
                        continue
                    
                    actual_model = model_name.replace('vertex/', '').replace('models/', '')
                    
                    if is_vertex:
                        # Используем Vertex SDK (уже инициализирован в __init__)
                        response = self.client.models.generate_content(model=actual_model, contents=full_prompt)
                        result = response.text
                    else:
                        # Используем Google Generative AI (нужен API ключ)
                        import google.generativeai as google_genai
                        genai_model = google_genai.GenerativeModel(actual_model)
                        response = genai_model.generate_content(full_prompt)
                        result = response.text
                else:
                    # OpenRouter или LiteRouter для текста
                    api_type = "literouter" if is_literouter else "openrouter"
                    api_key = os.getenv("LITEROUTER_API_KEY" if is_literouter else "OPENROUTER_API_KEY")
                    api_url = self.LITEROUTER_API_URL if is_literouter else self.OPENROUTER_API_URL
                    
                    if is_literouter:
                        # LiteRouter API URL обычно имеет /v1/chat/completions
                        if not api_url.endswith('/chat/completions'):
                            api_url = "https://api.literouter.com/v1/chat/completions"
                    
                    actual_model = model_name.replace('literouter/', '')
                    
                    if api_key:
                        resp = requests.post(
                            api_url,
                            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                            json={"model": actual_model, "messages": [{"role": "user", "content": full_prompt}]},
                            timeout=120
                        )
                        if resp.status_code == 200:
                            result = resp.json()["choices"][0]["message"]["content"]

                if not result:
                    continue

                cleaned_result = result.strip()
                if "```json" in cleaned_result:
                    cleaned_result = re.search(r'```json\s*(.*?)\s*```', cleaned_result, re.DOTALL).group(1)
                elif "```" in cleaned_result:
                    cleaned_result = re.search(r'```\s*(.*?)\s*```', cleaned_result, re.DOTALL).group(1)
                
                cleaned_result = cleaned_result.strip()
                
                try:
                    json.loads(cleaned_result)
                    workflow_db_manager.update_book_visual_bible_workflow(book_id, cleaned_result)
                    print(f"[ComicGenerator] Visual Bible created and saved for {book_id}")
                    return cleaned_result
                except:
                    match = re.search(r'\{.*\}', cleaned_result, re.DOTALL)
                    if match:
                        json_str = match.group(0)
                        workflow_db_manager.update_book_visual_bible_workflow(book_id, json_str)
                        return json_str
            except Exception as e:
                print(f"[ComicGenerator] Visual analysis attempt {level} failed: {e}")
        
        return None

    def _build_image_prompt(self, summary, visual_bible_raw, simplified=False):
        """Создает финальный промпт для генерации изображения."""
        if simplified:
            # Упрощенный промпт (для ретрая при ошибках фильтрации)
            base = (
                "Cinematic live-action film frame, photorealistic, realistic skin texture, "
                "dramatic lighting, high-end production look, ultra-detailed movie still aesthetic."
            )
        else:
            # Основной качественный промпт
            base = self.BASE_PROMPT

        visual_bible_prompt = ""
        if visual_bible_raw:
            try:
                bible_data = json.loads(visual_bible_raw)
                bible_list = [f"- {name}: {desc}" for name, desc in bible_data.items()]
                visual_bible_prompt = "\nREFERENCE FOR CHARACTERS (Follow these descriptions strictly):\n" + "\n".join(bible_list)
            except:
                pass

        return f"{base}\n\n{visual_bible_prompt}\n\n{'SCENE:' if simplified else 'TEXT TO ADAPT:'} {summary}"

    def process_book_comic(self, book_id, app_instance):
        """Цикл генерации комикса по всем секциям книги с сохранением в БД."""
        with app_instance.app_context():
            app_instance.logger.info(f"[ComicGenerator] Starting comic generation for book {book_id}")
            
            book_info = workflow_db_manager.get_book_workflow(book_id)
            if not book_info:
                app_instance.logger.error(f"[ComicGenerator] Book {book_id} not found. Exiting.")
                return
            
            sections = workflow_db_manager.get_sections_for_book_workflow(book_id)
            if not sections:
                app_instance.logger.warning(f"[ComicGenerator] No sections found for book {book_id}. Exiting.")
                return

            app_instance.logger.info(f"[ComicGenerator] Book {book_id} has {len(sections)} sections.")

            # ВАЖНО: Мы НЕ блокируем генерацию комикса, если основной анализ (глоссарий) ожидает редактирования.
            # Эти процессы теперь независимы.

            visual_bible_raw = book_info.get('visual_bible')
            if not visual_bible_raw:
                app_instance.logger.info(f"[ComicGenerator] Visual Bible missing for {book_id}. Running analysis...")
                visual_bible_raw = self._run_visual_analysis(book_id, sections)
                if visual_bible_raw:
                    app_instance.logger.info(f"[ComicGenerator] Visual Bible created for {book_id}. Stopping for edit.")
                    workflow_db_manager.update_book_comic_status_workflow(book_id, 'awaiting_bible_edit')
                    return
                else:
                    app_instance.logger.error(f"[ComicGenerator] Failed to create Visual Bible for {book_id}")
                    workflow_db_manager.update_book_comic_status_workflow(book_id, 'error')
                    return
            
            # Если статус был 'awaiting_bible_edit', значит мы уже его показали или пользователь нажал "Сделать комикс" повторно
            # Но на всякий случай убедимся, что статус 'processing' во время генерации картинок
            workflow_db_manager.update_book_comic_status_workflow(book_id, 'processing')

            for section in sections:
                section_id = section['section_id']
                # ВАЖНО: не загружаем BLOB, иначе растит RSS и может привести к OOM
                if workflow_db_manager.check_comic_image_exists(section_id):
                    continue

                summary = workflow_cache_manager.load_section_stage_result(book_id, section_id, 'summarize')
                if not summary or len(summary.strip()) < 50:
                    app_instance.logger.info(f"[ComicGenerator] Summary for section {section_id} is too short or empty. Skipping.")
                    continue
                
                app_instance.logger.info(f"[ComicGenerator] Generating image for section {section_id} (Summary length: {len(summary)})...")
                
                prompt = None
                image_data = None
                error = None
                try:
                    for attempt in range(2):
                        # Генерируем промпт через вспомогательный метод
                        prompt = self._build_image_prompt(summary, visual_bible_raw, simplified=(attempt > 0))

                        image_data, error = self.generate_image(prompt, book_id, section_id)
                        
                        if image_data:
                            workflow_db_manager.save_comic_image_workflow(book_id, section_id, image_data)
                            app_instance.logger.info(f"[ComicGenerator] Successfully saved comic to DB for section {section_id}")
                            break
                        elif error == "IMAGE_SAFETY" and attempt == 0:
                            app_instance.logger.warning(f"[ComicGenerator] Safety filter triggered for section {section_id}. Retrying with simplified prompt.")
                            continue
                        else:
                            app_instance.logger.error(f"[ComicGenerator] Permanent failure for section {section_id} (Error: {error}). Saving CENSORED placeholder.")
                            self._save_censored_placeholder(book_id, section_id)
                            break
                except Exception as e:
                    app_instance.logger.error(f"[ComicGenerator] Exception processing section {section_id}: {e}")
                finally:
                    # Явно освобождаем крупные объекты, чтобы не накапливать RSS на длинных задачах
                    try:
                        if prompt is not None:
                            del prompt
                        if summary is not None:
                            del summary
                        if image_data is not None:
                            del image_data
                        import gc
                        gc.collect()
                    except Exception:
                        pass
                
                time.sleep(5 + random.randint(1, 3))

            app_instance.logger.info(f"[ComicGenerator] Finished comic generation loop for book {book_id}")

    def _save_censored_placeholder(self, book_id, section_id):
        try:
            resp = requests.get(self.CENSORED_IMAGE_URL, timeout=10)
            if resp.status_code == 200:
                workflow_db_manager.save_comic_image_workflow(book_id, section_id, resp.content)
        except Exception as e:
            print(f"[ComicGenerator] Censored placeholder error: {e}")

def delete_comic_folder(book_id):
    pass
