# Конфигурация моделей для workflow с тремя уровнями fallback
# Уровень 1: primary - основная модель
# Уровень 2: fallback_level1 - первая резервная модель  
# Уровень 3: fallback_level2 - вторая резервная модель (последняя попытка)

MODEL_CONFIG = {
    'summarize': {
        'primary': 'nvidia/nemotron-3-nano-30b-a3b:free',
        'fallback_level1': 'models/gemma-3-27b-it:free',
        'fallback_level2': 'openrouter/free'
    },
    'analyze': {
        'primary': 'vertex/gemini-3-pro-preview',
        'fallback_level1': 'deepseek/deepseek-r1-0528:free', #'tngtech/deepseek-r1t2-chimera:free',
        'fallback_level2': 'tngtech/deepseek-r1t-chimera:free',
        'fallback_level3': 'models/gemini-3-flash-preview'
    },
    'translate': {
        'primary': 'vertex/gemini-3-flash-preview',
        'fallback_level1': 'vertex/gemini-3-pro-preview',
        'fallback_level2': 'deepseek/deepseek-r1-0528:free', #'tngtech/deepseek-r1t2-chimera:free',
        'fallback_level3': 'tngtech/deepseek-r1t-chimera:free'
    },
    'reduce': {
        'primary': 'deepseek/deepseek-r1-0528:free', #'tngtech/deepseek-r1t2-chimera:free',
        'fallback_level1': 'models/gemma-3-27b-it:free',
        'fallback_level2': 'google/gemma-3-27b-it:free'
    },
    'translate_toc': {
        'primary': 'models/gemini-3-flash-preview',
        'fallback_level1': 'deepseek/deepseek-r1-0528:free', #'tngtech/deepseek-r1t2-chimera:free',
        'fallback_level2': 'tngtech/deepseek-r1t-chimera:free'
    },
    'video_analyze': {
        'primary': 'deepseek/deepseek-r1-0528:free', #'tngtech/deepseek-r1t2-chimera:free',
        'fallback_level1': 'tngtech/deepseek-r1t-chimera:free',
        'fallback_level2': 'openrouter/free',
    },
    'video_chat': {
        'primary': 'deepseek/deepseek-r1-0528:free', #'tngtech/deepseek-r1t2-chimera:free',
        'fallback_level1': 'tngtech/deepseek-r1t-chimera:free',
        'fallback_level2': 'openrouter/free',
    },
    'title_translate': {
        'primary': 'deepseek/deepseek-r1-0528:free', #'tngtech/deepseek-r1t2-chimera:free',
        'fallback_level1': 'tngtech/deepseek-r1t-chimera:free',
        'fallback_level2': 'openrouter/free',
    },
    'football_predict': {
        'primary': 'vertex/gemini-3-pro-preview',
        'fallback_level1': 'deepseek/deepseek-r1-0528:free', #'tngtech/deepseek-r1t2-chimera:free',
        'fallback_level2': 'openrouter/free',
        'fallback_level3': 'models/gemini-3-flash-preview'
    },
    'bet_risk_analysis': {
        'primary': 'vertex/gemini-3-pro-preview',
        'fallback_level1': 'deepseek/deepseek-r1-0528:free', #'tngtech/deepseek-r1t2-chimera:free',
        'fallback_level2': 'openrouter/free',
        'fallback_level3': 'models/gemini-3-flash-preview'
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