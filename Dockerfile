# 사용할 기본 이미지 지정
FROM python:3.12-slim
ENV PYTHONUNBUFFERED 1

# 비-루트 사용자 생성 (예: 'appuser')
RUN groupadd -r appgroup && useradd -r -g appgroup -m -d /home/appuser appuser

# 컨테이너 내 작업 디렉토리 설정
WORKDIR /usr/src/app

# 소유권을 새 사용자에게 변경하고 파일 복사
# 먼저 requirements.txt만 복사하여 의존성 설치 (Docker 캐시 활용)
COPY --chown=appuser:appgroup requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# 나머지 애플리케이션 코드 복사
COPY --chown=appuser:appgroup . ./

# 비-루트 사용자로 전환
USER appuser

# 컨테이너가 사용할 포트 지정 (애플리케이션에 따라 다름)
EXPOSE 8080

# 컨테이너 시작 시 실행할 명령어 (예: main.py 실행)
CMD ["python", "main.py"]