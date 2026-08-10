import requests
from typing import List, Optional
import os
from PIL import Image
import io
from utils.logger_util import LoggerUtil
from dotenv import load_dotenv

load_dotenv()

class ApiError(Exception):
    """API 호출 관련 커스텀 예외"""
    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        self.message = message
        super().__init__(f"API Error (Status: {status_code}): {message}")

class ApiUtil:
    # 게시판 종류. 등록 엔드포인트(/api/board-research)와 조회 페이지(/board-research/{idx})가
    # 같은 이름을 쓰므로 한 곳에 둔다.
    BOARD = "board-research"

    def __init__(self):
        base_url = os.getenv("BASE_URL")
        if not base_url:
            raise EnvironmentError("환경 변수 'BASE_URL'가 설정되어 있지 않습니다.")

        base_url = base_url.rstrip("/")
        self.base_url = base_url          # 게시물 조회 URL 조립에 쓴다
        self.api_base_url = f"{base_url}/api"
        self.headers = {
            "Accept": "application/json"
        }
        self.max_file_size = 1 * 1024 * 1024  # 1MB
        self.max_width = 800  # 최대 너비
        self.logger = LoggerUtil().get_logger()

    def _compress_image(self, image_path: str):
        """이미지 압축"""
        try:
            with Image.open(image_path) as img:
                # 이미지 크기 조정
                if img.width > self.max_width:
                    ratio = self.max_width / img.width
                    new_height = int(img.height * ratio)
                    img = img.resize((self.max_width, new_height), Image.Resampling.LANCZOS)
                
                # 이미지 품질 조정
                buffer = io.BytesIO()
                format = img.format if img.format else 'PNG'
                
                if format == 'PNG':
                    img.save(buffer, format=format, optimize=True)
                else:
                    img.save(buffer, format=format, quality=85, optimize=True)
                
                compressed_image = buffer.getvalue()
                
                # 압축 후에도 크기가 큰 경우 추가 압축
                quality = 85
                while len(compressed_image) > self.max_file_size and quality > 30:
                    buffer = io.BytesIO()
                    img.save(buffer, format='JPEG', quality=quality, optimize=True)
                    compressed_image = buffer.getvalue()
                    quality -= 10
                
                self.logger.info(f"이미지 압축 완료: {image_path} (크기: {len(compressed_image)/1024:.1f}KB)")
                return compressed_image, format.lower()
        except Exception as e:
            self.logger.error(f"이미지 압축 실패: {image_path} - {str(e)}")
            raise

    def board_post_url(self, response: Optional[dict]) -> Optional[str]:
        """게시글 생성 응답에서 게시물 조회 URL을 만든다. 만들 수 없으면 None.

        서버(mqway)는 생성된 게시글 idx를 data.id 로 돌려주고
        (BoardApiController::processStore), 조회 페이지 라우트는
        board-research.show = /board-research/{idx} 다. 회원 전용이라 열람에는
        로그인이 필요하다.
        """
        data = (response or {}).get("data") or {}
        post_id = data.get("id")
        if post_id is None:
            self.logger.warning("응답에 게시글 id가 없어 게시물 URL을 만들지 못했습니다.")
            return None
        return f"{self.base_url}/{self.BOARD}/{post_id}"

    def create_post(self, title: str, content: str, category: str, writer: str, image_paths: Optional[List[str]] = None, thumbnail_image_path: str = None):
        """게시글 생성 API 호출 (이미지/썸네일 유무와 관계없이 단일 흐름)"""
        url = f"{self.api_base_url}/{self.BOARD}"

        try:
            data = {
                "title": title,
                "content": content,
                "category": category,
                "writer": writer
            }

            files = {}
            intended_images = bool(image_paths)

            # 본문 이미지 처리
            if image_paths:
                self.logger.info(f"게시글 생성 시작 (이미지 포함) - 제목: {title}")
                for i, image_path in enumerate(image_paths):
                    if os.path.exists(image_path):
                        try:
                            compressed_image, format = self._compress_image(image_path)
                            original_filename = os.path.basename(image_path)
                            files[f"image[{i}]"] = (original_filename, compressed_image, f"image/{format}")
                            self.logger.debug(f"이미지 {i+1} 추가: {original_filename}")
                        except Exception as e:
                            self.logger.error(f"이미지 처리 실패: {image_path} - {str(e)}")
                            continue
                    else:
                        self.logger.warning(f"이미지 경로가 존재하지 않습니다: {image_path}")

            # 썸네일 이미지 처리 (있으면 추가)
            if thumbnail_image_path and os.path.exists(thumbnail_image_path):
                try:
                    compressed_image, format = self._compress_image(thumbnail_image_path)
                    thumbnail_filename = os.path.basename(thumbnail_image_path)
                    files["thumbnail_image"] = (thumbnail_filename, compressed_image, f"image/{format}")
                    self.logger.debug(f"썸네일 이미지 추가: {thumbnail_filename}")
                except Exception as e:
                    self.logger.error(f"썸네일 이미지 처리 실패: {thumbnail_image_path} - {str(e)}")
            elif thumbnail_image_path:
                self.logger.warning(f"썸네일 경로가 존재하지 않습니다: {thumbnail_image_path}")

            # 사용자가 이미지 업로드를 의도했지만, 실사용할 파일이 하나도 없는 경우 에러
            if intended_images and not any(k.startswith("image[") for k in files.keys()) and "thumbnail_image" not in files:
                error_msg = "처리 가능한 이미지가 없습니다."
                self.logger.error(error_msg)
                raise ApiError(400, error_msg)

            response = None
            
            # multipart/form-data 또는 JSON 전송 결정
            if files:
                try:
                    headers = self.headers.copy()
                    form_data = {key: (None, str(value)) for key, value in data.items()}
                    form_data.update(files)
                    self.logger.debug(f"API 요청 데이터: {data}")
                    self.logger.debug(f"파일 데이터: {[f'{k}: {v[0]}' for k, v in files.items()]}")
                    self.logger.debug(f"최종 전송 데이터: {[(k, v[0] if isinstance(v, tuple) else v) for k, v in form_data.items()]}")

                    response = requests.post(
                        url,
                        headers=headers,
                        files=form_data,
                        timeout=30
                    )

                    self.logger.debug(f"응답 상태 코드: {response.status_code}")
                    self.logger.debug(f"응답 헤더: {dict(response.headers)}")
                finally:
                    files.clear()
            else:
                self.logger.info(f"게시글 생성 시작 (이미지 없음) - 제목: {title}")
                # timeout 을 빼면 서버가 응답하지 않을 때 무한 대기한다.
                # HTML 리포트 전송은 이미지가 없어 항상 이 경로를 탄다.
                response = requests.post(url, headers=self.headers, json=data, timeout=30)

            # 응답 확인 및 한글 디코딩
            try:
                response.encoding = 'utf-8'
                response_data = response.json()

                self.logger.debug(f"API 응답: {response_data}")

                if not response_data.get('success', False):
                    error_msg = f"게시글 생성 실패\n제목: {title}\n카테고리: {category}\n응답: {response.text}"
                    self.logger.error(error_msg)
                    raise ApiError(response.status_code, error_msg)

                self.logger.info(f"게시글 생성 성공 - 제목: {title}")

                # 이미지 URL 확인 로직
                if intended_images and not response_data.get('data', {}).get('image_urls'):
                    self.logger.warning(f"이미지가 포함된 게시글이지만 image_urls가 비어있습니다. - 제목: {title}")
                elif intended_images:
                    self.logger.info(f"이미지 URL: {response_data.get('data', {}).get('image_urls')}")

                return response_data

            except ValueError:
                error_msg = f"JSON 응답 파싱 실패\n제목: {title}\n카테고리: {category}\n응답: {response.text}"
                self.logger.error(error_msg)
                raise ApiError(response.status_code, error_msg)

        except requests.RequestException as e:
            error_msg = f"API 요청 중 오류 발생\n제목: {title}\n카테고리: {category}\n오류: {str(e)}"
            self.logger.error(error_msg)
            raise ApiError(500, error_msg)

if __name__ == "__main__":
    # 연결 확인용 수동 테스트. 실제 게시글이 하나 생성되므로 주의.
    # 이 프로젝트는 이미지 없이 HTML 본문만 보내므로 그 경로만 확인한다.
    import sys

    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(errors="replace")   # 한국어 콘솔(cp949) 대응
        except Exception:
            pass

    try:
        api = ApiUtil()
    except EnvironmentError as e:
        print(f"설정 오류: {e}")
        sys.exit(1)

    try:
        result = api.create_post(
            title="API 전송 테스트",
            content="<p>HTML 본문 전송 테스트입니다.</p>",
            category="시황",
            writer="admin",
        )
        print("API 호출 결과:", result)
    except ApiError as e:
        print(f"API 에러 발생: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"예상치 못한 에러 발생: {type(e).__name__}: {e}")
        sys.exit(1)
