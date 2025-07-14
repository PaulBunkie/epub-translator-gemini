# Конфигурация моделей для workflow с тремя уровнями fallback
# Уровень 1: primary - основная модель
# Уровень 2: fallback_level1 - первая резервная модель  
# Уровень 3: fallback_level2 - вторая резервная модель (последняя попытка)

MODEL_CONFIG = {
    'summarize': {
        'primary': 'models/gemini-2.5-flash-preview-05-20',
        'fallback_level1': 'meta-llama/llama-3.3-70b-instruct:free',
        'fallback_level2': 'MODEL_NAME_HERE'
    },
    'analyze': {
        'primary': 'models/gemini-2.5-flash-preview-05-20',
        'fallback_level1': 'microsoft/mai-ds-r1:free',
        'fallback_level2': 'MODEL_NAME_HERE'
    },
    'translate': {
        'primary': 'models/gemini-2.5-flash-preview-05-20',
        'fallback_level1': 'microsoft/mai-ds-r1:free',
        'fallback_level2': 'MODEL_NAME_HERE'
    },
    'reduce': {
        'primary': 'models/gemini-2.5-flash-preview-05-20',
        'fallback_level1': 'meta-llama/llama-3.3-70b-instruct:free',
        'fallback_level2': 'MODEL_NAME_HERE'
    },
    'translate_toc': {
        'primary': 'models/gemini-2.5-flash-preview-05-20',
        'fallback_level1': 'google/gemma-3-27b-it:free',
        'fallback_level2': 'MODEL_NAME_HERE'
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