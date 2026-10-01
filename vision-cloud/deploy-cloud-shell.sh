#!/usr/bin/env bash
# Run in Google Cloud Shell, after selecting an existing Firebase Blaze project.
set -euo pipefail
cd "$(dirname "$0")"
: "${VISION_PROJECT:?Set VISION_PROJECT to your Firebase project ID.}"
: "${VISION_OWNER_UID:?Set VISION_OWNER_UID to your Firebase Authentication user UID.}"
: "${VISION_FIREBASE_API_KEY:?Set VISION_FIREBASE_API_KEY to the Firebase web app apiKey (public config).}"
VISION_REGION="${VISION_REGION:-us-central1}"
VISION_SERVICE=vision-cloud
VISION_BUCKET="${VISION_PROJECT}-vision-results"
VISION_RUNTIME="vision-runtime@${VISION_PROJECT}.iam.gserviceaccount.com"
VISION_TASK="vision-task@${VISION_PROJECT}.iam.gserviceaccount.com"
VISION_BUILD="vision-build@${VISION_PROJECT}.iam.gserviceaccount.com"
gcloud config set project "$VISION_PROJECT"
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com cloudtasks.googleapis.com firestore.googleapis.com storage.googleapis.com firebase.googleapis.com identitytoolkit.googleapis.com securetoken.googleapis.com iamcredentials.googleapis.com
for VISION_ACCOUNT in vision-runtime vision-task vision-build; do
  gcloud iam service-accounts describe "${VISION_ACCOUNT}@${VISION_PROJECT}.iam.gserviceaccount.com" >/dev/null 2>&1 || gcloud iam service-accounts create "$VISION_ACCOUNT"
done
for VISION_ROLE in roles/datastore.user roles/cloudtasks.enqueuer roles/firebaseauth.viewer; do
  gcloud projects add-iam-policy-binding "$VISION_PROJECT" --member="serviceAccount:${VISION_RUNTIME}" --role="$VISION_ROLE" --condition=None --quiet >/dev/null
done
gcloud iam service-accounts add-iam-policy-binding "$VISION_TASK" --member="serviceAccount:${VISION_RUNTIME}" --role=roles/iam.serviceAccountUser --quiet >/dev/null
gcloud beta services identity create --service=cloudtasks.googleapis.com --project="$VISION_PROJECT" >/dev/null
VISION_PROJECT_NUMBER="$(gcloud projects describe "$VISION_PROJECT" --format='value(projectNumber)')"
gcloud iam service-accounts add-iam-policy-binding "$VISION_TASK" --member="serviceAccount:service-${VISION_PROJECT_NUMBER}@gcp-sa-cloudtasks.iam.gserviceaccount.com" --role=roles/iam.serviceAccountTokenCreator --quiet >/dev/null
# The source deployment build account requires these on new projects.
for VISION_ROLE in roles/run.builder roles/logging.logWriter roles/artifactregistry.writer roles/storage.objectViewer; do
  gcloud projects add-iam-policy-binding "$VISION_PROJECT" --member="serviceAccount:${VISION_BUILD}" --role="$VISION_ROLE" --condition=None --quiet >/dev/null
done
gcloud firestore databases describe --database='(default)' >/dev/null 2>&1 || gcloud firestore databases create --database='(default)' --location="$VISION_REGION" --type=firestore-native --quiet
gcloud storage buckets describe "gs://${VISION_BUCKET}" >/dev/null 2>&1 || gcloud storage buckets create "gs://${VISION_BUCKET}" --location="$VISION_REGION" --uniform-bucket-level-access --public-access-prevention
gcloud storage buckets add-iam-policy-binding "gs://${VISION_BUCKET}" --member="serviceAccount:${VISION_RUNTIME}" --role=roles/storage.objectAdmin >/dev/null
cat > /tmp/vision-lifecycle.json <<'JSON'
{"rule":[{"action":{"type":"Delete"},"condition":{"age":1}}]}
JSON
gcloud storage buckets update "gs://${VISION_BUCKET}" --lifecycle-file=/tmp/vision-lifecycle.json --soft-delete-duration=0
for VISION_COLLECTION in visionJobs visionQuotas visionResponses; do
  gcloud firestore fields ttls update expiresAt --collection-group="$VISION_COLLECTION" --enable-ttl --quiet
done
gcloud tasks queues describe vision-media --location="$VISION_REGION" >/dev/null 2>&1 || gcloud tasks queues create vision-media --location="$VISION_REGION"
gcloud tasks queues update vision-media --location="$VISION_REGION" --max-concurrent-dispatches=1 --max-dispatches-per-second=1 --max-attempts=30 --min-backoff=60s --max-backoff=300s --max-retry-duration=7200s
# SERVICE_URL is added immediately after the first deployment gives us its address.
gcloud run deploy "$VISION_SERVICE" --source=. --build-service-account="projects/${VISION_PROJECT}/serviceAccounts/${VISION_BUILD}" --region="$VISION_REGION" --allow-unauthenticated --service-account="$VISION_RUNTIME" --memory=2Gi --cpu=1 --concurrency=4 --min-instances=0 --max-instances=1 --timeout=1800 --set-env-vars="GOOGLE_CLOUD_PROJECT=${VISION_PROJECT},REGION=${VISION_REGION},RESULT_BUCKET=${VISION_BUCKET},OWNER_UIDS=${VISION_OWNER_UID},TASK_SERVICE_ACCOUNT=${VISION_TASK},TASK_QUEUE=vision-media,DAILY_IMPORT_LIMIT=20" --quiet
VISION_URL="$(gcloud run services describe "$VISION_SERVICE" --region="$VISION_REGION" --format='value(status.url)')"
gcloud run services update "$VISION_SERVICE" --region="$VISION_REGION" --update-env-vars="SERVICE_URL=${VISION_URL}" --quiet
gcloud run services add-iam-policy-binding "$VISION_SERVICE" --region="$VISION_REGION" --member="serviceAccount:${VISION_TASK}" --role=roles/run.invoker --quiet >/dev/null
export VISION_URL VISION_PROJECT VISION_FIREBASE_API_KEY
python3 - <<'PY'
import json, os
from pathlib import Path
config = {'backendUrl': os.environ['VISION_URL'], 'projectId': os.environ['VISION_PROJECT'], 'firebaseApiKey': os.environ['VISION_FIREBASE_API_KEY']}
Path('vision-cloud-config.json').write_text(json.dumps(config, indent=2) + '\n')
print('\nVision Cloud endpoint: ' + config['backendUrl'])
print('Public app configuration saved to vision-cloud-config.json. Import it into Vision Cloud settings. No private keys were generated.')
PY
