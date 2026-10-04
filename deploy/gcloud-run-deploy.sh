# Example Cloud Run deploy. Do not run until the operator approves.
#
# Prerequisites:
#   gcloud config set project YOUR_GCP_PROJECT
#   Secret Manager secret named gemini-api-key containing GEMINI_API_KEY
#   Artifact Registry repo (example: us-central1-docker.pkg.dev/YOUR_GCP_PROJECT/qa-engine/qa-engine)

# IMAGE=us-central1-docker.pkg.dev/YOUR_GCP_PROJECT/qa-engine/qa-engine:latest
# gcloud run deploy qa-engine \
#   --project YOUR_GCP_PROJECT \
#   --region us-central1 \
#   --image "$IMAGE" \
#   --min-instances 1 \
#   --max-instances 1 \
#   --cpu 2 \
#   --memory 4Gi \
#   --timeout 3600 \
#   --no-cpu-throttling \
#   --execution-environment gen2 \
#   --concurrency 20 \
#   --port 8080 \
#   --set-env-vars "GEMMA_PROVIDER=gemini,GEMINI_MODEL_ID=gemini-3.5-flash,GEMMA_MAX_OUTPUT_TOKENS=4096,BROWSER_ADAPTER=direct_playwright,PLAYWRIGHT_HEADLESS=true,PLAYWRIGHT_DOCKER=1,SERVE_FRONTEND=true,DEBUG=false,ALLOW_LOCAL_TARGETS=false,HOST=0.0.0.0" \
#   --set-secrets "GEMINI_API_KEY=gemini-api-key:latest" \
#   --quiet
