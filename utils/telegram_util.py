import os
from urllib.request import urlopen
import urllib.parse
import requests
from dotenv import load_dotenv
import json
import inspect

load_dotenv()

class TelegramUtil:
    def __init__(self):
        self.bot_token = os.getenv('TELEGRAM_BOT_TOKEN')
        self.chat_id = os.getenv('TELEGRAM_CHAT_ID')
        self.chat_test_id = os.getenv('TELEGRAM_CHAT_TEST_ID')

    def send_message(self, message):
        """일반 메시지 전송"""
        message = urllib.parse.quote_plus(message)
        urlopen(f"https://api.telegram.org/bot{self.bot_token}/sendMessage?chat_id={self.chat_id}&parse_mode=html&text={message}")

    def send_photo(self, photo_path, caption=""):
        """이미지 전송"""
        url = f"https://api.telegram.org/bot{self.bot_token}/sendPhoto"
        
        with open(photo_path, 'rb') as photo:
            payload = {
                "chat_id": self.chat_id,
                "caption": caption,
                "parse_mode": "html"
            }
            files = {
                "photo": photo
            }
            response = requests.post(url, data=payload, files=files)
        
        return response.json()

    def send_test_message(self, message):
        """테스트용 채팅방으로 메시지 전송 (에러 발생 위치 포함)"""
        try:
            caller_frame = inspect.stack()[1]
            caller_file = caller_frame.filename

            project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            project_name = os.path.basename(project_root)
            rel_path = os.path.relpath(caller_file, project_root).replace("\\", "/")

            postfix = f"PATH : {project_name}/{rel_path}"
            full_message = f"{message}\n{postfix}"
        except Exception:
            # 경로 계산 실패 시 원본 메시지만 전송
            full_message = message

        encoded_message = urllib.parse.quote_plus(full_message)
        urlopen(f"https://api.telegram.org/bot{self.bot_token}/sendMessage?chat_id={self.chat_test_id}&parse_mode=html&text={encoded_message}") 
    
    def send_multiple_photo(self, photo_paths, caption=""):
        """여러 장의 이미지 한 번에 전송"""
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMediaGroup"
        
        media = []
        files = {}
        
        # 각 이미지에 대한 미디어 객체 생성
        for index, photo_path in enumerate(photo_paths):
            # 첫 번째 이미지에만 캡션 추가
            media_caption = caption if index == 0 else ""
            
            media.append({
                'type': 'photo',
                'media': f'attach://photo{index}',
                'caption': media_caption,
                'parse_mode': 'html'
            })
            
            files[f'photo{index}'] = open(photo_path, 'rb')
        
        try:
            payload = {
                'chat_id': self.chat_id,
                'media': json.dumps(media)
            }
            
            response = requests.post(url, data=payload, files=files)
            for file in files.values():
                file.close()
            
            return response.json()
            
        except Exception as e:
            # 에러 발생시에도 파일들을 확실히 닫아줌
            for file in files.values():
                file.close()
            raise e 