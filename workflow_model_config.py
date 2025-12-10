# Конфигурация моделей для workflow с тремя уровнями fallback
# Уровень 1: primary - основная модель
# Уровень 2: fallback_level1 - первая резервная модель  
# Уровень 3: fallback_level2 - вторая резервная модель (последняя попытка)

MODEL_CONFIG = {
    'summarize': {
        'primary': 'qwen/qwen-2.5-72b-instruct:free',
        'fallback_level1': 'amazon/nova-2-lite-v1:free',
        'fallback_level2': 'google/gemma-3-27b-it:free'
    },
    'analyze': {
        'primary': 'models/gemini-3-pro-preview',
        'fallback_level1': 'tngtech/deepseek-r1t2-chimera:free',
        'fallback_level2': 'tngtech/deepseek-r1t-chimera:free'
    },
    'translate': {
        'primary': 'tngtech/deepseek-r1t-chimera:free',
        'fallback_level1': 'tngtech/deepseek-r1t2-chimera:free',
        'fallback_level2': 'models/gemini-3-pro-preview'
    },
    'reduce': {
        'primary': 'meta-llama/llama-3.1-405b-instruct:free',
        'fallback_level1': 'amazon/nova-2-lite-v1:free',
        'fallback_level2': 'google/gemma-3-27b-it:free'
    },
    'translate_toc': {
        'primary': 'models/gemini-flash-latest',
        'fallback_level1': 'tngtech/tng-r1t-chimera:free',
        'fallback_level2': 'tngtech/deepseek-r1t2-chimera:free'
    },
    'video_analyze': {
        'primary': 'amazon/nova-2-lite-v1:free',
        'fallback_level1': 'tngtech/tng-r1t-chimera:free',
        'fallback_level2': 'tngtech/deepseek-r1t2-chimera:free'
    },
    'video_chat': {
        'primary': 'amazon/nova-2-lite-v1:free',
        'fallback_level1': 'tngtech/tng-r1t-chimera:free',
        'fallback_level2': 'tngtech/deepseek-r1t2-chimera:free'
    },
    'title_translate': {
        'primary': 'amazon/nova-2-lite-v1:free',
        'fallback_level1': 'tngtech/tng-r1t-chimera:free',
        'fallback_level2': 'tngtech/deepseek-r1t2-chimera:free'
    },
    'football_predict': {
        'primary': 'tngtech/tng-r1t-chimera:free',
        'fallback_level1': 'tngtech/deepseek-r1t2-chimera:free',
        'fallback_level2': 'amazon/nova-2-lite-v1:free',
        'fallback_level3': 'openai/gpt-5.1'
    },
    'bet_risk_analysis': {
        'primary': 'tngtech/tng-r1t-chimera:free',
        'fallback_level1': 'tngtech/deepseek-r1t2-chimera:free',
        'fallback_level2': 'amazon/nova-2-lite-v1:free',
        'fallback_level3': 'openai/gpt-5.1'
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