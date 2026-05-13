# Конфигурация моделей для workflow с тремя уровнями fallback
# Уровень 1: primary - основная модель
# Уровень 2: fallback_level1 - первая резервная модель  
# Уровень 3: fallback_level2 - вторая резервная модель (последняя попытка)

DEFAULT_MODEL = "openrouter/free"

MODEL_CONFIG = {
    'summarize': {
        'primary': 'nvidia/nemotron-3-nano-30b-a3b:free', #'nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free',
        'fallback_level1': 'nvidia/nemotron-3-super-120b-a12b:free', # 'models/gemma-3-27b-it:free',
        'fallback_level2': 'openrouter/free'
    },
    'analyze': {
        'primary': 'literouter/gemini-3-flash-preview-thinking',  
        'fallback_level1': 'openrouter/owl-alpha', #'tngtech/deepseek-r1t2-chimera:free',
        'fallback_level2': 'nvidia/nemotron-3-super-120b-a12b:free',
        'fallback_level3': 'vertex/gemini-3-flash-preview'
    },
    'translate': {
        'primary': 'literouter/gemini-3-flash-preview-thinking', #'vertex/gemini-3-flash-preview', #'literouter/grok-4.1', #'literouter/deepseek-v4-pro-thinking:full-context', 
        'fallback_level1': 'literouter/gpt-5.4-mini', #'models/gemini-3-flash-preview', #'tngtech/deepseek-r1t2-chimera:free',
        'fallback_level2': 'literouter/grok-4.1', #'arcee-ai/trinity-large-thinking:free',        
        'fallback_level3': 'openrouter/owl-alpha' #'minimax/minimax-m2.5:free',        
    },
    'reduce': {
        'primary': 'literouter/deepseek-v4-pro-thinking:full-context', #'openrouter/owl-alpha',  
        'fallback_level1': 'nvidia/nemotron-3-super-120b-a12b:free',
        'fallback_level2': 'openrouter/free'
    },
    'translate_toc': {
        'primary': 'literouter/claude-haiku-4.5:free', #'openrouter/owl-alpha',
        'fallback_level1': 'nvidia/nemotron-3-super-120b-a12b:free', #'tngtech/deepseek-r1t2-chimera:free',
        'fallback_level2': 'openrouter/free',
        'fallback_level3': 'vertex/gemini-3-flash-preview'
    },
    'video_analyze': {
        'primary': 'literouter/claude-haiku-4.5:free', #'tngtech/deepseek-r1t2-chimera:free',
        'fallback_level1': 'nvidia/nemotron-3-super-120b-a12b:free',
        'fallback_level2': 'openrouter/free'
    },
    'video_chat': {
        'primary': 'openrouter/owl-alpha', #'tngtech/deepseek-r1t2-chimera:free',
        'fallback_level1': 'nvidia/nemotron-3-super-120b-a12b:free',
        'fallback_level2': 'openrouter/free'
    },
    'title_translate': {
        'primary': 'literouter/claude-haiku-4.5:free', 
        'fallback_level1': 'nvidia/nemotron-3-nano-30b-a3b:free',
        'fallback_level2': 'openrouter/free'
    },
    'football_predict': {
        'primary': 'nvidia/nemotron-3-super-120b-a12b:free',
        'fallback_level1': 'openrouter/owl-alpha', #'tngtech/deepseek-r1t2-chimera:free',
        'fallback_level2': 'openrouter/free',
        'fallback_level3': 'vertex/gemini-3-pro-preview'
    },
    'bet_risk_analysis': {
        'primary': 'nvidia/nemotron-3-super-120b-a12b:free',
        'fallback_level1': 'openrouter/owl-alpha', #'models/gemini-3-flash-preview', #'tngtech/deepseek-r1t2-chimera:free',
        'fallback_level2': 'openrouter/free',
        'fallback_level3': 'vertex/gemini-3-pro-preview'
    },
    'generate_comic': {
        'primary': 'bytedance-seed/seedream-4.5',
        'fallback_level1': 'sourceful/riverflow-v2-fast',
        'fallback_level2': 'vertex/gemini-2.5-flash-image'
    },
    'visual_analysis': {
        'primary': 'vertex/gemini-3-flash-preview', 
        'fallback_level1': 'nvidia/nemotron-3-super-120b-a12b:free',
        'fallback_level2': 'openrouter/free'
    },
    'person_locations': {
        'primary': 'nvidia/nemotron-3-nano-30b-a3b:free', 
        'fallback_level1': 'literouter/claude-haiku-4.5:free',
        'fallback_level2': 'openrouter/free'
    }
}

def get_model_for_operation(operation_type: str, level: str) -> str:
    """
    Возвращает модель для указанной операции и уровня.
    
    Args:
        operation_type: Тип операции ('summarize', 'analyze', 'translate', 'reduce', 'translate_toc')
        level: Уровень модели ('primary', 'fallback_level1', 'fallback_level2')
    
    Returns:
        Имя модели или None, если не найдено
    """
    if operation_type not in MODEL_CONFIG:
        return None
    
    return MODEL_CONFIG[operation_type].get(level)

def get_all_models_for_operation(operation_type: str) -> dict:
    """
    Возвращает все модели для указанной операции.
    
    Args:
        operation_type: Тип операции
    
    Returns:
        Словарь с моделями для всех уровней
    """
    return MODEL_CONFIG.get(operation_type, {}) 