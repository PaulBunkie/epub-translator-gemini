# Конфигурация моделей для workflow с динамическими уровнями fallback
# Уровни: primary -> fallback_level1 -> fallback_level2 -> ... -> fallback_level(N)

DEFAULT_MODEL = "openrouter/free"

MODEL_CONFIG = {
    'summarize': {
        'primary': 'literouter/claude-haiku-4.5-cheap:free', 
        'fallback_level1': 'nvidia/nemotron-3-nano-30b-a3b:free', 
        'fallback_level2': 'nvidia/nemotron-3-super-120b-a12b:free',
        'fallback_level3': 'literouter/gpt-5.4-mini'

    },
    'analyze': {
        'primary': 'literouter/gemini-3-flash-preview-thinking',  
        'fallback_level1': 'vertex/gemini-3-flash-preview', 
        'fallback_level2': 'literouter/claude-haiku-4.5-cheap:free',
        'fallback_level3': 'nvidia/nemotron-3-ultra-550b-a55b:free', 
        'fallback_level4': 'nvidia/nemotron-3-super-120b-a12b:free'
    },
    'translate': {
        'primary': 'literouter/gemini-3-flash-preview', 
        'fallback_level1': 'vertex/gemini-3-flash-preview', 
        'fallback_level2': 'literouter/gpt-5.4-mini',
        'fallback_level3': 'literouter/minimax-m2.7:free',
        'fallback_level4': 'literouter/deepseek-v3.2:free',  
        'fallback_level5': 'literouter/claude-haiku-4.5-cheap:free',
        'fallback_level6': 'nvidia/nemotron-3-ultra-550b-a55b:free'         
    },
    'reduce': {
        'primary': 'literouter/deepseek-v4-pro-thinking:full-context', #'openrouter/owl-alpha', 
        'fallback_level1': 'vertex/gemini-3-flash-preview', 
        'fallback_level2': 'literouter/claude-haiku-4.5-cheap:free', 
        'fallback_level3': 'literouter/gemini-3-flash-preview-thinking',
        'fallback_level4': 'nvidia/nemotron-3-ultra-550b-a55b:free'
    },
    'translate_toc': {
        'primary': 'literouter/gemini-3-flash-preview',
        'fallback_level1': 'vertex/gemini-3-flash-preview', 
        'fallback_level2': 'literouter/claude-haiku-4.5-cheap:free',  
        'fallback_level3': 'nvidia/nemotron-3-ultra-550b-a55b:free'
    },
    'video_analyze': {
        'primary': 'literouter/claude-haiku-4.5-cheap:free', 
        'fallback_level1': 'nvidia/nemotron-3-super-120b-a12b:free',
        'fallback_level2': 'nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free',
        'fallback_level3': 'nvidia/nemotron-3-nano-30b-a3b:free',
        'fallback_level4': 'openrouter/free'
    },
    'video_chat': {
        'primary': 'literouter/claude-haiku-4.5-cheap:free', 
        'fallback_level1': 'nvidia/nemotron-3-super-120b-a12b:free',
        'fallback_level2': 'nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free',
        'fallback_level3': 'nvidia/nemotron-3-nano-30b-a3b:free',
        'fallback_level4': 'openrouter/free'
    },
    'title_translate': {
        'primary': 'literouter/claude-haiku-4.5-cheap:free', 
        'fallback_level1': 'nvidia/nemotron-3-super-120b-a12b:free',
        'fallback_level2': 'nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free'
    },
    'football_predict': {
        'primary': 'literouter/claude-haiku-4.5-cheap:free', 
        'fallback_level1': 'nvidia/nemotron-3-super-120b-a12b:free',
        'fallback_level2': 'nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free',
        'fallback_level3': 'nvidia/nemotron-3-nano-30b-a3b:free',
        'fallback_level4': 'openrouter/free',
        'fallback_level5': 'vertex/gemini-3-pro-preview'
    },
    'bet_risk_analysis': {
        'primary': 'literouter/claude-haiku-4.5-cheap:free', 
        'fallback_level1': 'nvidia/nemotron-3-super-120b-a12b:free',
        'fallback_level2': 'nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free',
        'fallback_level3': 'nvidia/nemotron-3-nano-30b-a3b:free',
        'fallback_level4': 'openrouter/free',
        'fallback_level5': 'vertex/gemini-3-pro-preview'
    },
    'generate_comic': {
        'primary': 'bytedance-seed/seedream-4.5',
        'fallback_level1': 'sourceful/riverflow-v2-fast',
        'fallback_level2': 'vertex/gemini-2.5-flash-image'
    },
    'visual_analysis': {
        'primary': 'literouter/gemini-3-flash-preview-thinking', 
        'fallback_level1': 'models/gemini-3-flash-preview',
        'fallback_level2': 'vertex/gemini-3-flash-preview', 
        'fallback_level3': 'literouter/claude-haiku-4.5-cheap:free',
        'fallback_level4': 'nvidia/nemotron-3-ultra-550b-a55b:free'
    },
    'person_locations': {
        'primary': 'literouter/claude-haiku-4.5:free',
        'fallback_level1': 'nvidia/nemotron-3-nano-30b-a3b:free', 
        'fallback_level2': 'nvidia/nemotron-3-ultra-550b-a55b:free'
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