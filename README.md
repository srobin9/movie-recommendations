# movie-recommendations
Google Cloud Activated Shell 기준

## Initial Set up
### 환경 변수 설정
```
export PROJECT_ID=$GOOGLE_CLOUD_PROJECT
export REGION=us-central1

export GEMINI_MODEL=gemini-1.5-flash-002
export TEXT_EMBEDDING_MODEL=text-embedding-005
```

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
   --project $PROJECT_ID
```
## AlloyDB Setup
### AlloyDB 생성
```
if [ -z "$(gcloud alloydb instances list --project=$PROJECT_ID)" ]; then
  gcloud compute addresses create psa-range \
      --global \
      --purpose=VPC_PEERING \
      --prefix-length=16 \
      --description="ip range for service networking" \
      --network=default \
      --project=$PROJECT_ID

  gcloud services vpc-peerings connect \
      --service=servicenetworking.googleapis.com \
      --ranges=psa-range \
      --network=default \
      --project=$PROJECT_ID

  gcloud alloydb clusters create movies-cluster \
      --region $GCP_REGION \
      --password "movies-demo-password" \
      --project $PROJECT_ID

  gcloud alloydb instances create movies-instance \
    --instance-type=PRIMARY \
    --cpu-count=4 \
    --region=$GCP_REGION \
    --cluster=movies-cluster \
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
    --zone=$GCP_REGION-b \
    --network=default \
    --subnet=default \
    --scopes="https://www.googleapis.com/auth/cloud-platform" \
    --machine-type=e2-medium
```
#### GCE Instance에 접속
```
gcloud compute ssh psql-admin --project $PROJECT_ID --zone $GCP_REGION-b
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

### 실습 코드 복사
```
git clone https://github.com/srobin9/movie-recommendations
cd ~/movie-recommendations
```

# Artifact Registry 에 Docker 저장소 생성
```
gcloud artifacts repositories create docker-repo \
  --repository-format=docker \
  --location=$REGION \
  --description="Docker repository" \
  --project=$PROJECT_ID
```

# Cloud Build 를 활용하여 컨테이너 빌드
```
gcloud builds submit --tag=${REGION}-docker.pkg.dev/${PROJECT_ID}/docker-repo/movie-recommendation
```

# Google Kubernetes Engine
### GKE Autopilot 클러스터 생성
```
gcloud container clusters create-auto $CLUSTER \
    --location=$REGION --async
```

### GKE 클러스터 인증
```
gcloud container clusters get-credentials $CLUSTER --region $REGION
```

### K8s Config (Deployment, Service) 에 환경변수 값 적용
```
sed -i 's/${K8S_SERVICE_ACCOUNT}/'${K8S_SERVICE_ACCOUNT}'/g' k8s.yaml
sed -i 's/${REGION}/'${REGION}'/g' k8s.yaml
sed -i 's/${PROJECT_ID}/'${PROJECT_ID}'/g' k8s.yaml
sed -i 's/${GEMINI_MODEL}/'${GEMINI_MODEL}'/g' k8s.yaml
```

### Workload Identity Federation for GKE
```
# Vertex AI 호출을 위한 SA 생성
gcloud iam service-accounts create ${GCP_SERVICE_ACCOUNT} \
 --project ${PROJECT_ID}

# Vertex AI 호출을 위한 Rule 부여
gcloud projects add-iam-policy-binding ${PROJECT_ID}  \
 --member "serviceAccount:${GCP_SERVICE_ACCOUNT}@${PROJECT_ID}.iam.gserviceaccount.com"  \
 --role "roles/aiplatform.user"

# GSA 와 KSA 바인딩
gcloud iam service-accounts add-iam-policy-binding ${GCP_SERVICE_ACCOUNT}@${PROJECT_ID}.iam.gserviceaccount.com \
 --role roles/iam.workloadIdentityUser \
 --member "serviceAccount:${PROJECT_ID}.svc.id.goog[default/${K8S_SERVICE_ACCOUNT}]"

# GSA 와 KSA 바인딩 (KSA 에 annotation 추가)
kubectl annotate serviceaccount ${K8S_SERVICE_ACCOUNT} \
iam.gke.io/gcp-service-account=${GCP_SERVICE_ACCOUNT}@${PROJECT_ID}.iam.gserviceaccount.com
```

### K8s Config (Deployment, Service) 배포
```
kubectl apply -f k8s.yaml
```

### 테스트
```
export ENDPOINT=[External-ip of Service]

curl -X POST -H "Content-Type: application/json" -d '{
  "movies": ["Despicable Me 4", "Inside Out 2"],
  "scenario": "가족들과 함께 보기 좋은"
}' "$ENDPOINT/recommendations"
```

# Cloud run
### Cloud run 에 배포
```
gcloud run deploy mr-run \
--image ${REGION}-docker.pkg.dev/${PROJECT_ID}/docker-repo/movie-recommendation  \
--region ${REGION}  \
--set-env-vars PROJECT_ID=${PROJECT_ID},REGION=${REGION},GEMINI_MODEL=${GEMINI_MODEL} \
--service-account ${GCP_SERVICE_ACCOUNT}@${PROJECT_ID}.iam.gserviceaccount.com  \
--allow-unauthenticated
```

### 테스트
```
export ENDPOINT=[Endpoint of Cloud Run Service]

curl -X POST -H "Content-Type: application/json" -d '{
  "movies": ["Despicable Me 4", "Inside Out 2"],
  "scenario": "가족들과 함께 보기 좋은"
}' "$ENDPOINT/recommendations"
```
