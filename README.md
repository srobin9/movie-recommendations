# movie-recommendations
Google Cloud Activated Shell 기준으로 실행하고 테스트하는 법을 안내합니다. 

## Initial Set up
### Google Cloud Activated Shell Setup
```
gcloud auth login
gcloud config set project <PROJECT_ID>
```
### 환경 변수 설정
```
export PROJECT_ID=<PROJECT ID>
export REGION=us-central1
export GCP_SERVICE_ACCOUNT=movie-recommendations
export GEMINI_MODEL=gemini-2.0-flash
export TEXT_EMBEDDING_MODEL=text-embedding-005
```
* Gemini Model과 Text Embedding Model은 수시로 버전이 업데이트되니 실행 전에 문서를 확인해서 최신 버전을 사용하시기 바랍니다.
* Gemini Model Version[https://ai.google.dev/gemini-api/docs/models?hl=ko]
* Text Embedding Model Version[https://cloud.google.com/vertex-ai/generative-ai/docs/model-reference/text-embeddings-api?hl=ko]

### 서비스 활성화
```
gcloud services enable \
   aiplatform.googleapis.com \
   alloydb.googleapis.com \
   cloudbuild.googleapis.com \
   cloudtrace.googleapis.com \
   run.googleapis.com \
   servicenetworking.googleapis.com \
   storage.googleapis.com \
   --project=$PROJECT_ID
```
## AlloyDB Setup
### AlloyDB 생성
```
if [ -z "$(gcloud alloydb instances list --project=$PROJECT_ID)" ]; then
  gcloud compute addresses create psa-range \
      --global \
      --purpose       = VPC_PEERING \
      --prefix-length = 16 \
      --description   = "ip range for service networking" \
      --network       = default \
      --project       = $PROJECT_ID

  gcloud services vpc-peerings connect \
      --service = servicenetworking.googleapis.com \
      --ranges  = psa-range \
      --network = default \
      --project = $PROJECT_ID

  gcloud alloydb clusters create movies-cluster \
      --region $REGION \
      --password "movies-demo-password" \
      --project $PROJECT_ID

  gcloud alloydb instances create movies-instance \
      --instance-type   = PRIMARY \
      --cpu-count       = 4 \
      --region          = $REGION \
      --cluster         = movies-cluster \
      --project $PROJECT_ID

else
  echo "AlloyDB has already been created for you"
fi
```

### AlloyDB 데이터베이스와 테이블 구성
#### AlloyDB에 데이터 설정하기 위한 GCE Instnace 생성
```
gcloud compute instances create psql-admin \
    --project=$PROJECT_ID \
    --image-family=debian-12 \
    --image-project=debian-cloud \
    --zone=$REGION-b \
    --network=default \
    --subnet=default \
    --scopes="https://www.googleapis.com/auth/cloud-platform" \
    --machine-type=e2-medium \
    --shielded-secure-boot
```
#### GCE Instance에 접속
```
gcloud compute ssh psql-admin --project $PROJECT_ID --zone $REGION-b
```

#### PostgreSQL Client 설치
```
sudo apt-get install -y postgresql-client
export PROJECT_ID=$(curl -H "Metadata-Flavor:Google" http://metadata.google.internal/computeMetadata/v1/project/project-id)
export PGPASSWORD=movies-demo-password
export GCP_REGION=us-central1
export ALLOYDB_ENDPOINT_NAME=movies-endpoint
export ALLOYDB_INSTANCE_IP=$(gcloud alloydb instances describe movies-instance --cluster movies-cluster --region $GCP_REGION --project $PROJECT_ID --format="value(ipAddress)")
```
#### movies 데이터베이스 생성 및 PGvctor extension 활성화
```
psql -U postgres -h $ALLOYDB_INSTANCE_IP -c 'create DATABASE movies'
psql -U postgres -h $ALLOYDB_INSTANCE_IP -d movies -c 'CREATE EXTENSION IF NOT EXISTS alloydb_scann CASCADE;'
```
#### Embeddins 저장용 테이블 생성 
```
cat <<EOF | psql -U postgres -h $ALLOYDB_INSTANCE_IP -d movies
  CREATE TABLE IF NOT EXISTS
movie_titles(
    langchain_id UUID NOT NULL,
    content TEXT NOT NULL,
    embedding VECTOR(768) NOT NULL,
    show_id VARCHAR,
    type VARCHAR,
    country VARCHAR,
    date_added VARCHAR,
    release_year INTEGER,
    rating VARCHAR,
    duration VARCHAR,
    listed_in VARCHAR,
    langchain_metadata JSON not NULL
    );
EOF
```
#### Prepopulated embeddings dataset을 테이블에 복사
```
gsutil cp gs://cloud-samples-data/langchain/alloydb/netflix_titles_embeddings.csv .
psql -U postgres -h $ALLOYDB_INSTANCE_IP -d movies -c '\COPY movie_titles FROM ./netflix_titles_embeddings.csv CSV HEADER'
```
#### GCE Instance 접속 종료
```
exit
```

## Code Build & Image Creation
### 실습 코드 복사
```
git clone https://github.com/srobin9/movie-recommendations
cd ~/movie-recommendations
```

### Artifact Registry 에 Docker 저장소 생성
```
gcloud artifacts repositories create docker-repo \
  --repository-format=docker \
  --location=$REGION \
  --description="Docker repository" \
  --project=$PROJECT_ID
```

### Cloud Build 를 활용하여 컨테이너 빌드
```
gcloud builds submit --tag=${REGION}-docker.pkg.dev/${PROJECT_ID}/docker-repo/movie-recommendations
```

## CloudRun Setup

### CloudRun에서 사용할 Service Account 생성 및 권한 부여
```
# Vertex AI 호출을 위한 SA 생성
gcloud iam service-accounts create $GCP_SERVICE_ACCOUNT \
  --description "our famous recommendation service" \
  --project $PROJECT_ID

# Vertex AI 호출을 위한 Role 부여
gcloud projects add-iam-policy-binding $PROJECT_ID  \
  --member "serviceAccount:$GCP_SERVICE_ACCOUNT@$PROJECT_ID.iam.gserviceaccount.com"  \
  --role "roles/aiplatform.user"

# DB 연결을 위한 Role 부여
gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:${GCP_SERVICE_ACCOUNT}@${PROJECT_ID}.iam.gserviceaccount.com" \
  --role="roles/alloydb.client"
```

### Cloud run에 배포
```
echo "Cloud Run 서비스 재배포 (Direct VPC Egress 사용)..."
gcloud run deploy movie-recommendations \
  --image ${REGION}-docker.pkg.dev/${PROJECT_ID}/docker-repo/movie-recommendations \
  --region ${REGION} \
  --set-env-vars PROJECT_ID=${PROJECT_ID},REGION=${REGION},GEMINI_MODEL=${GEMINI_MODEL},TEXT_EMBEDDING_MODEL=${TEXT_EMBEDDING_MODEL} \
  --allow-unauthenticated \
  --max-instances 3 \
  --service-account ${GCP_SERVICE_ACCOUNT}@${PROJECT_ID}.iam.gserviceaccount.com \
  --network=default \
  # --subnet=default # 특정 서브넷 지정이 필요하면 주석 해제 (일반적으로 네트워크만 지정해도 됨)
  --vpc-egress=private-ranges-only \
  --project $PROJECT_ID

```

## 테스트
```
#CloudRun URL확인
CLOUD_RUN_ENDPOINT=$(gcloud run services describe movie-recommendations --region $REGION --format='value(status.url)' --project $PROJECT_ID)
echo $CLOUD_RUN_ENDPOINT
#테스트 수행
curl -X POST -H "Content-Type: application/json" -d '{
  "movies": ["Despicable Me 4", "Inside Out 2"],
  "scenario": "가족과 함께 보기 좋은"
}' "$CLOUD_RUN_ENDPOINT/recommendations"
```
## Resources Clean-up
아래 처럼 clean_up.sh 파일을 생성합니다. 
```
# clean_up.sh
# 사용자 확인 (선택 사항이지만 안전을 위해 권장)
read -p "정말로 $PROJECT_ID 프로젝트의 리소스를 삭제하시겠습니까? (y/N) " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]
then
    echo "리소스 삭제를 취소했습니다."
    exit 1
fi

# 1. Cloud Run 서비스 삭제
echo "Cloud Run 서비스 삭제 중: movie-recommendations..."
gcloud run services delete movie-recommendations \
  --region=${REGION} \
  --project=$PROJECT_ID \
  --quiet

# 2. Artifact Registry Docker 저장소 삭제 (내부 이미지 포함)
echo "Artifact Registry 저장소 삭제 중: docker-repo..."
gcloud artifacts repositories delete docker-repo \
  --location=$REGION \
  --project=$PROJECT_ID \
  --quiet

# 3. GCE 인스턴스 삭제 (psql-admin)
echo "GCE 인스턴스 삭제 중: psql-admin..."
gcloud compute instances delete psql-admin \
  --zone=$REGION-b \
  --project=$PROJECT_ID \
  --quiet

# 4. AlloyDB 인스턴스 삭제
# 주의: 인스턴스 삭제는 시간이 다소 걸릴 수 있습니다.
echo "AlloyDB 인스턴스 삭제 중: movies-instance..."
gcloud alloydb instances delete movies-instance \
  --cluster=movies-cluster \
  --region=$REGION \
  --project=$PROJECT_ID \
  --quiet

# 5. AlloyDB 클러스터 삭제
# 주의: 클러스터 삭제는 시간이 다소 걸릴 수 있습니다.
# 인스턴스가 완전히 삭제된 후 실행해야 할 수 있습니다. 잠시 기다린 후 실행하세요.
echo "AlloyDB 클러스터 삭제 대기 중 (약 1분)..."
sleep 60
echo "AlloyDB 클러스터 삭제 중: movies-cluster..."
gcloud alloydb clusters delete movies-cluster \
  --region=$REGION \
  --project=$PROJECT_ID \
  --force \
  --quiet

# 6. 서비스 네트워킹용 예약된 IP 주소(PSA Range) 삭제
# 주의: AlloyDB 클러스터가 완전히 삭제된 후 실행해야 할 수 있습니다.
# 만약 'resource is being used' 오류가 발생하면 잠시 후 다시 시도하세요.
echo "PSA IP 주소 범위 삭제 대기 중 (약 1분)..."
sleep 60
echo "Compute Address (PSA Range) 삭제 중: psa-range..."
gcloud compute addresses delete psa-range \
  --global \
  --project=$PROJECT_ID \
  --quiet

# 7. VPC 피어링 연결 해제 (선택 사항 - PSA Range 삭제로 충분할 수 있음)
# 일반적으로 PSA Range를 삭제하면 관련 피어링도 정리될 수 있으나, 명시적으로 해제할 수도 있습니다.
# echo "VPC 피어링 연결 해제 중..."
# gcloud services vpc-peerings delete \
#     --service=servicenetworking.googleapis.com \
#     --network=default \
#     --project=$PROJECT_ID \
#     --quiet
# 참고: vpc-peerings delete 대신 update --remove-peering을 사용해야 할 수도 있습니다.
# PSA Range 삭제 후 문제가 없다면 이 단계는 생략해도 무방합니다.

# 8. 서비스 계정 삭제
echo "IAM 서비스 계정 삭제 중: $GCP_SERVICE_ACCOUNT..."
gcloud iam service-accounts delete ${GCP_SERVICE_ACCOUNT}@${PROJECT_ID}.iam.gserviceaccount.com \
  --project=$PROJECT_ID \
  --quiet
# 참고: 서비스 계정에 연결된 IAM 정책 바인딩(roles/aiplatform.user)은 서비스 계정이 삭제되면 자동으로 처리됩니다.

# 9. 로컬 실습 코드 디렉토리 삭제 (선택 사항)
echo "로컬 코드 디렉토리 삭제 중: ~/movie-recommendations..."
rm -rf ~/movie-recommendations

echo "리소스 정리가 완료되었습니다."
echo "Google Cloud Console (https://console.cloud.google.com/) 에서 $PROJECT_ID 프로젝트를 확인하여 모든 리소스가 정상적으로 삭제되었는지 확인하는 것이 좋습니다."
```
위에서 생성한 shell script를 실행하여 생성한 Google Cloud 자원들을 삭제합니다. 
```
chmod 777 clean_up.sh
sh ./clean.sh
```
