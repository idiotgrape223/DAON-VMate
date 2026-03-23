import os
import yaml

def load_config():
    """
    settings.yaml 파일을 읽어서 딕셔너리로 반환합니다.
    """
    config_path = os.path.join(os.path.dirname(__file__), 'settings.yaml')
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            return yaml.safe_load(f)
    except Exception as e:
        print(f"[Config] 설정 파일 로드 실패: {e}")
        return {}

def save_config(config_dict):
    """
    딕셔너리를 settings.yaml 파일에 저장합니다.
    """
    config_path = os.path.join(os.path.dirname(__file__), 'settings.yaml')
    try:
        with open(config_path, 'w', encoding='utf-8') as f:
            yaml.dump(config_dict, f, allow_unicode=True, sort_keys=False)
        return True
    except Exception as e:
        print(f"[Config] 설정 파일 저장 실패: {e}")
        return False
