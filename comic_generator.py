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
from google import genai
from google.genai import types
import workflow_db_manager
import workflow_cache_manager
import workflow_model_config
import workflow_translation_module

class ComicGenerator:
    CENSORED_IMAGE_URL = "https://upload.wikimedia.org/wikipedia/commons/thumb/7/70/Censored_rubber_stamp.svg/960px-Censored_rubber_stamp.svg.png"
    OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"

    # Промпты инкапсулированы внутри модуля
    VISUAL_ANALYSIS_SYSTEM_PROMPT = """You are an expert in visual character design and literary analysis. Your task is to create a "Visual Bible" for a book based on its summaries.
        
CRITICAL RULES:
1. Identify all recurring or important characters and entities.
2. For each, provide a detailed visual description in ENGLISH.
3. Focus on: age, gender, ethnicity, build, hair style/color, distinctive facial features, typical clothing style, and specific accessories.
4. IMPORTANT: If the text doesn't specify visual details, INFER them logically based on the character's role, personality, and context.
5. STYLE ADHERENCE: All descriptions must be compatible with a "Modern European Digital Comic / Bande Dessinée" style.
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
                self.client = genai.Client(
                    vertexai=True,
                    project=project_id,
                    location="global"
                )
                print(f"[ComicGenerator] Client initialized for project {project_id} (Location: global)")
            else:
                print("[ComicGenerator] No project_id found, client not initialized.")
        except Exception as e:
            print(f"[ComicGenerator] Error initializing GenAI Client: {e}")
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
                # СТРОГОЕ определение провайдера
                is_vertex = model_name.startswith('vertex/') or model_name.startswith('models/') or 'gemini' in model_name.lower()
                
                if is_vertex:
                    if self.client:
                        actual_model = model_name.replace('vertex/', '').replace('models/', '')
                        # Объединяем промпт для Vertex
                        response = self.client.models.generate_content(model=actual_model, contents=full_prompt)
                        result = response.text
                else:
                    # OpenRouter для текста
                    api_key = os.getenv("OPENROUTER_API_KEY")
                    if api_key:
                        resp = requests.post(
                            self.OPENROUTER_API_URL,
                            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                            json={"model": model_name, "messages": [{"role": "user", "content": full_prompt}]},
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

    def process_book_comic(self, book_id, app_instance):
        """Цикл генерации комикса по всем секциям книги с сохранением в БД."""
        with app_instance.app_context():
            print(f"[ComicGenerator] Starting comic generation for book {book_id}")
            
            book_info = workflow_db_manager.get_book_workflow(book_id)
            sections = workflow_db_manager.get_sections_for_book_workflow(book_id)
            if not sections:
                return

            # ВАЖНО: Мы НЕ запускаем генерацию комикса, пока анализ находится в статусе 'awaiting_edit'
            book_stage_statuses = book_info.get('book_stage_statuses', {})
            analyze_status = book_stage_statuses.get('analyze', {}).get('status')
            
            if analyze_status == 'awaiting_edit':
                print(f"[ComicGenerator] Анализ книги {book_id} ожидает редактирования. Пропускаем генерацию комикса.")
                return

            visual_bible_raw = book_info.get('visual_bible')
            if not visual_bible_raw:
                visual_bible_raw = self._run_visual_analysis(book_id, sections)
            
            visual_bible_prompt = ""
            if visual_bible_raw:
                try:
                    bible_data = json.loads(visual_bible_raw)
                    bible_list = [f"- {name}: {desc}" for name, desc in bible_data.items()]
                    visual_bible_prompt = "\nREFERENCE FOR CHARACTERS (Follow these descriptions strictly):\n" + "\n".join(bible_list)
                except:
                    pass

            BASE_PROMPT = (
                "Draw a dynamic modern comic adaptation of the text in 6–10 sequential panels. "
                "Short dialogue (1–3 words per bubble) allowed. No captions, no narration, no internal monologue, no long text. "
                "Do not use evenly spaced rectangular panels. Use an asymmetrical, contemporary layout with varied panel sizes, "
                "angled or overlapping frames, and occasional full-bleed panels. "
                "Tell the story through action, movement, body language, lighting, environment, and cinematic camera shifts "
                "(close-ups, wide shots, low angles, Dutch tilt). Each panel must show clear progression and escalating tension. "
                "Style: bold, kinetic, high-end modern graphic novel, Studio Ghibli inspired graphic."
            )

            for section in sections:
                section_id = section['section_id']
                if workflow_db_manager.get_comic_image_workflow(section_id):
                    continue

                summary = workflow_cache_manager.load_section_stage_result(book_id, section_id, 'summarize')
                if not summary or len(summary.strip()) < 50:
                    continue

                for attempt in range(2):
                    if attempt == 0:
                        prompt = f"{BASE_PROMPT}\n\n{visual_bible_prompt}\n\nTEXT TO ADAPT: {summary}"
                    else:
                        print(f"[ComicGenerator] Retrying with simplified prompt for section {section_id} due to failure/filter...")
                        prompt = f"Dynamic modern comic illustration, Studio Ghibli inspired style, safe for all ages.\n\n{visual_bible_prompt}\n\nSCENE: {summary}"

                    image_data, error = self.generate_image(prompt, book_id, section_id)
                    
                    if image_data:
                        workflow_db_manager.save_comic_image_workflow(book_id, section_id, image_data)
                        print(f"[ComicGenerator] Successfully saved comic to DB for section {section_id}")
                        break
                    elif error == "IMAGE_SAFETY" and attempt == 0:
                        continue
                    else:
                        print(f"[ComicGenerator] Permanent failure for section {section_id}. Saving CENSORED.")
                        self._save_censored_placeholder(book_id, section_id)
                        break
                
                time.sleep(30 + random.randint(1, 5))

            print(f"[ComicGenerator] Finished comic generation for book {book_id}")

    def _save_censored_placeholder(self, book_id, section_id):
        try:
            resp = requests.get(self.CENSORED_IMAGE_URL, timeout=10)
            if resp.status_code == 200:
                workflow_db_manager.save_comic_image_workflow(book_id, section_id, resp.content)
        except Exception as e:
            print(f"[ComicGenerator] Censored placeholder error: {e}")

def delete_comic_folder(book_id):
    pass
