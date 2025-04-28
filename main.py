# main.py 상단에 추가
from google.cloud import storage
import logging # 로깅 추가

# -*- coding: utf-8 -*-
# 필요한 라이브러리들을 가져옵니다.
import os  # 운영 체제와 상호 작용하기 위한 모듈 (환경 변수 접근 등)
# 웹 애플리케이션 구축을 위한 Flask 프레임워크 관련 모듈
from flask import Flask, request, jsonify, render_template
# Vertex AI Safety Setting 사용
from vertexai.generative_models import HarmCategory, HarmBlockThreshold
# LangChain에서 Vertex AI의 임베딩 및 LLM 모델을 사용하기 위한 모듈
from langchain_google_vertexai import VertexAIEmbeddings, VertexAI
# LangChain에서 AlloyDB for PostgreSQL 벡터 저장소 및 엔진을 사용하기 위한 모듈             
from langchain_google_alloydb_pg import AlloyDBVectorStore, AlloyDBEngine
# LLM에 전달할 프롬프트의 템플릿을 만들기 위한 모듈        
from langchain_core.prompts import PromptTemplate
# 여러 문서를 하나의 프롬프트로 결합하는 체인을 만들기 위한 모듈                  
from langchain.chains.combine_documents import create_stuff_documents_chain
# 검색(retrieval)과 생성(generation)을 결합하는 체인 (현재 코드에서는 직접 사용되지 않음)             
# from langchain.chains import create_retrieval_chain
# LangChain에서 텍스트 문서 단위를 나타내는 객체                   
from langchain_core.documents import Document                                 

# --- 기본 설정 ---
# Google Cloud 프로젝트 ID를 환경 변수에서 가져옵니다. Cloud Run 배포 시 설정됩니다.
project_id              = os.environ["PROJECT_ID"]
region                  = os.environ["REGION"]
gemini_model            = os.environ["GEMINI_MODEL"]
text_embedding_model    = os.environ["TEXT_EMBEDDING_MODEL"]
# AlloyDB 인스턴스, 클러스터, 리전 정보 설정
instance_name           = "movies-instance"         # 연결할 AlloyDB 인스턴스 이름
cluster_name            = "movies-cluster"          # 해당 인스턴스가 속한 클러스터 이름
# AlloyDB 연결 정보
alloy_user              = "postgres"                # AlloyDB 접속 사용자 이름
alloy_password          = "movies-demo-password"    # AlloyDB 접속 비밀번호 (보안상 환경 변수나 Secret Manager 사용 권장)
database                = "movies"                  # 연결할 데이터베이스 이름
vector_table_name       = "movie_titles"            # 벡터 검색에 사용할 테이블 이름
iptype                  = "PRIVATE"                 # AlloyDB 접속 시 사용할 IP 타입 (PRIVATE: VPC 내부 IP 사용)

# --- 언어 모델 (LLM) 안전 설정 정의 ---
# Vertex AI 모델의 안전 설정을 정의합니다. 특정 카테고리의 유해 콘텐츠 생성을 차단하는 임계값을 설정합니다.
# 모든 유해 콘텐츠 카테고리(혐오 발언, 위험한 콘텐츠, 성적인 내용, 괴롭힘)에 대 임계값을 설정하고 있습니다.
safety_settings_config = {
    HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE,
    HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE,
    HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE,
    HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE,
}

# --- 언어 모델 (LLM) 초기화 ---
# Vertex AI의 Gemini 모델을 설정합니다.
llm = VertexAI(
    model_name          = gemini_model,           # 사용할 LLM 모델 이름 (예: gemini-1.5-flash, gemini-1.5-pro 등 확인 필요)
    project             = project_id,             # LLM을 사용할 Google Cloud 프로젝트 ID
    max_output_tokens   = 8192,                   # LLM이 생성할 수 있는 최대 토큰 수 (답변 길이 제한)
    safety_settings     = safety_settings_config  # 위에서 정의한 안전 설정을 적용
    # temperature         = 0.0                   # 모델의 창의성 조절 (0.0은 가장 결정론적, 높을수록 다양/무작위적) - 필요시 주석 해제
)

# AlloyDB 초기화 코드 전에 테스트 코드 추가
try:
    storage_client = storage.Client(project=project_id)
    buckets = storage_client.list_buckets()
    logging.warning("Successfully listed GCS buckets (Service Account Auth OK):") # WARNING 레벨로 변경하여 로그 확인 용이하게
    # for bucket in buckets: # 너무 많은 로그를 피하기 위해 주석 처리
    #     logging.warning(bucket.name)
except Exception as e:
    logging.error(f"Failed to list GCS buckets: {e}", exc_info=True) # 오류 발생 시 상세 로그 출력
    
# --- AlloyDB 엔진 초기화 ---
# AlloyDB for PostgreSQL 데이터베이스에 연결하기 위한 엔진 객체를 생성합니다.
# google-cloud-alloydb-connector 라이브러리를 사용하여 안전하게 연결합니다.
engine = AlloyDBEngine.from_instance(
   project_id   = project_id,       # AlloyDB 인스턴스가 있는 Google Cloud 프로젝트 ID
   instance     = instance_name,    # 연결할 AlloyDB 인스턴스 이름
   region       = region,           # AlloyDB 인스턴스가 있는 리전
   cluster      = cluster_name,     # 인스턴스가 속한 클러스터 이름
   database     = database,         # 연결할 데이터베이스 이름
   user         = alloy_user,       # 데이터베이스 접속 사용자 이름
   password     = alloy_password,   # 데이터베이스 접속 비밀번호
   ip_type      = iptype            # 접속 IP 타입 (PRIVATE)
)

# --- 임베딩 서비스 초기화 ---
# 텍스트를 벡터로 변환하는 Vertex AI 임베딩 모델을 설정합니다.
embeddings_service = VertexAIEmbeddings(
  model_name    = text_embedding_model, # 사용할 텍스트 임베딩 모델 이름 (Vertex AI 문서에서 최신/적합 모델 확인)
  project       = project_id               # 임베딩 모델을 사용할 Google Cloud 프로젝트 ID
)

# --- AlloyDB 벡터 저장소 초기화 ---
# AlloyDB 테이블을 LangChain 벡터 저장소로 사용하도록 설정합니다.
# create_sync는 동기 방식으로 벡터 저장소를 생성/연결합니다.
vector_store = AlloyDBVectorStore.create_sync(
   engine               = engine,               # 위에서 초기화한 AlloyDB 엔진 사용
   embedding_service    = embeddings_service,   # 위에서 초기화한 임베딩 서비스 사용 (텍스트 -> 벡터 변환)
   table_name           = vector_table_name,    # 벡터 데이터가 저장된 테이블 이름
   metadata_columns     = [                     # 벡터 검색 시 함께 가져올 메타데이터 컬럼 목록
       "show_id",
       "type",
       "country",
       "date_added",
       "release_year",
       "duration",
       "listed_in",
   ],
)

# --- 프롬프트 템플릿 정의 ---
# LLM에게 작업을 지시하는 기본 프롬프트 템플릿입니다.
# {movies}, {scenario}, {context} 부분은 나중에 실제 값으로 채워집니다.
prompt = PromptTemplate.from_template("""
당신은 영화 전문가 입니다.

다음 주어진 영화들 ({movies})과 유사하며, 주어진 시나리오("{scenario}")에 가장 잘 맞는 영화를 아래 '영화 라이브러리로부터의 유사한 영화' 목록에서 찾아 추천해야 합니다.

* **반드시** 아래 목록에 있는 영화 중에서 시나리오에 가장 적합하다고 판단되는 영화를 **최소 1개 이상** 선택하여 추천하고, 그 이유를 설명해주세요.
* 추천 시 영화의 duration, release_year, show_id 정보를 함께 제공해야 합니다.
* 만약 아래 목록의 영화들이 시나리오에 정말로 적합하지 않다고 판단되면, 그 이유를 명확하게 설명해주세요. 그렇지 않다면 반드시 목록 내에서 추천해야 합니다.
* 질문에 답하기 위해 입력된 프롬프트의 모든 정보를 활용하세요.

영화 라이브러리로부터의 유사한 영화 :

```{context}
```
""")

# --- Retriever (검색기) 설정 ---
# 벡터 저장소를 LangChain의 Retriever 인터페이스로 변환합니다.
# Retriever는 주어진 쿼리와 가장 유사한 문서를 벡터 저장소에서 검색하는 역할을 합니다.
retriever = vector_store.as_retriever(
  search_type   = "mmr",    # 검색 유형 설정: "mmr" (Maximal Marginal Relevance) - 관련성과 다양성 균형
                            # 또는 "similarity" (순수 유사도) 사용 가능
  search_kwargs = {         # 검색 관련 추가 인자
      "k": 5,               # 검색할 문서의 개수
      "lambda_mult": 0.8    # MMR 사용 시 관련성 가중치 (0~1 사이, 높을수록 관련성 중시)
                            # search_type="similarity" 사용 시 lambda_mult 불필요
  }
)

# --- 사용자 정의 문서 프롬프트 ---
# 검색된 각 문서를 LLM에게 전달하기 전에 형식을 지정하는 프롬프트입니다.
# {context} 안에 들어갈 개별 문서의 모양을 정의합니다.
customDocumentPrompt = PromptTemplate.from_template("""
Result:

Summary:
{page_content}

Metadata:
show_id: {show_id}
release_year: {release_year}
duration: {duration}
""")

# --- 문서 결합 및 생성 체인 설정 ---
# 검색된 문서들을 처리하고 최종 답변을 생성하는 LangChain 체인을 만듭니다.
# create_stuff_documents_chain: 검색된 모든 문서를 `{context}`에 넣고 LLM을 한 번 호출하는 가장 간단한 방식
combine_docs_chain = create_stuff_documents_chain(
  llm,                                      # 사용할 언어 모델 (위에서 정의한 Gemini)
  prompt,                                   # 사용할 메인 프롬프트 템플릿
  document_prompt = customDocumentPrompt    # 검색된 각 문서를 포맷할 때 사용할 프롬프트
)

# --- Flask 웹 애플리케이션 설정 ---
# Flask 애플리케이션 객체를 생성합니다.
app = Flask(__name__)
# jsonify 사용 시 비 ASCII 문자(한글 등)가 유니코드 이스케이프(\uXXXX)되지 않고 그대로 보이도록 설정합니다.
app.config['JSON_AS_ASCII'] = False

# --- API 엔드포인트 정의 ---
# '/recommendations' 경로로 POST 요청이 오면 이 함수가 처리합니다.
@app.route('/recommendations', methods=['POST'])
def movie_recommendations():
   """
   사용자 입력을 기반으로 영화 추천 목록을 반환합니다.
   """
   # 요청 본문이 JSON 형식이 아니면 400 오류를 반환합니다.
   if not request.json:
       return jsonify({'error': 'Missing JSON payload'}), 400

   # 요청 JSON 본문에서 'movies'와 'scenario' 값을 가져옵니다.
   # .get()을 사용하면 키가 없을 때 KeyError 대신 None을 반환하여 더 안전합니다. (추가적인 오류 처리 로직 권장)
   movies_list  = request.json.get('movies')
   scenario     = request.json.get('scenario')

   # 입력값 유효성 검사 : 입력값이 유효한지(존재하는지, 타입이 맞는지 등) 확인합니다. 부적절하면 400 오류 응답을 보냅니다.
   if not movies_list or not isinstance(movies_list, list) or not scenario:
       return jsonify({'error': 'Missing or invalid "movies" list or "scenario" in JSON payload'}), 400

   # 영화 목록을 문자열로 결합합니다 (LLM 프롬프트 및 검색 쿼리에 사용).
   movies_string = ", or ".join(movies_list) # "or" 뒤에 공백 추가

   # --- 벡터 검색 수행 ---
   # AlloyDB 벡터 저장소에서 입력 시나리오 및 영화와 유사한 영화를 검색합니다.
   search_query     = f"Movie that is great for {scenario} and similar to {movies_string}"
   print(f"Executing similarity search with query: {search_query}")                                 # 디버깅용 로그 출력
   retrieved_docs   = vector_store.similarity_search(search_query, k=5)                             # Retriever 설정 대신 직접 호출도 가능
   print(f"Retrieved documents ({len(retrieved_docs)}):")                                           # 디버깅용 로그 출력
   for i, doc in enumerate(retrieved_docs):
        print(f"  Doc {i+1}: ID={doc.metadata.get('show_id')}, Title={doc.page_content[:100]}...")  # 검색된 문서 정보 일부 출력

   # --- LLM 체인 실행 ---
   # 검색된 문서(context)와 사용자 입력(scenario, movies)을 LLM 체인에 전달하여 최종 응답을 생성합니다.
   response = combine_docs_chain.invoke({
       "scenario"   : scenario,
       "movies"     : movies_string,
       "context"    : retrieved_docs # 검색된 문서 목록을 context로 전달
   })

   # LLM이 생성한 응답을 반환합니다. (형식에 따라 jsonify 필요할 수 있음)
   # 현재는 LLM의 raw 출력을 그대로 반환하고 있습니다.
   # 더 구조화된 JSON 응답을 원하면 response 내용을 파싱하여 jsonify로 감싸는 것이 좋습니다.
   # 예: return jsonify({"recommendation": response})
   return response

# --- API 엔드포인트 정의: 상태 확인 (Health Check) ---
# '/healthz' 라는 URL 경로로 GET 요청이 들어오면 이 함수가 실행됩니다.
# 주로 클라우드 환경에서 서비스가 정상적으로 실행 중인지 확인하는 용도로 사용됩니다. (로드 밸런서, 오케스트레이션 도구 등)
@app.route('/healthz')
def healthz():
    return 'OK', 200

# --- 메인 실행 블록 ---
# 이 스크립트가 직접 실행될 때 (예: python main.py) 아래 코드가 실행됩니다.
# Gunicorn과 같은 WSGI 서버를 통해 실행될 때는 이 블록이 실행되지 않습니다.
if __name__ == '__main__':
   # 환경 변수 'PORT'가 있으면 해당 값을 사용하고, 없으면 기본값 8080을 사용합니다.
   port = int(os.environ.get('PORT', 8080))
   # Flask 개발 서버를 실행합니다.
   # debug=True는 개발 중에 유용하지만, 프로덕션 환경에서는 False로 설정해야 합니다.
   # host='0.0.0.0'은 모든 네트워크 인터페이스에서 접속을 허용합니다.
   app.run(debug=True, host='0.0.0.0', port=port)