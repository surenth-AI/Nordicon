#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# deploy.sh  –  Axe Global / Nordic-ON Session Keeper
# Builds the Docker image, pushes to Azure Container Registry,
# and creates (or updates) an Azure Container App Job on a 25-minute schedule.
#
# Run once to deploy. Azure handles all future executions automatically.
# Prerequisites: Azure CLI installed and logged in (az login)
# ─────────────────────────────────────────────────────────────────────────────

set -e

# ── EDIT THESE ────────────────────────────────────────────────────────────────
RESOURCE_GROUP="axe-nordic-rg"
LOCATION="northeurope"                        # closest to Norway
ACR_NAME="axenordicacr"                       # must be globally unique, lowercase
ENVIRONMENT_NAME="axe-nordic-env"
JOB_NAME="nordic-session-keeper"
STORAGE_ACCOUNT_NAME="axenordicsession"       # must be globally unique, lowercase
CONTAINER_NAME="axe-session"
BLOB_NAME="auth_state.json"
# ─────────────────────────────────────────────────────────────────────────────

echo "=== 1. Create Resource Group ==="
az group create --name $RESOURCE_GROUP --location $LOCATION

echo "=== 2. Create Container Registry ==="
az acr create \
  --resource-group $RESOURCE_GROUP \
  --name $ACR_NAME \
  --sku Basic \
  --admin-enabled true

echo "=== 3. Build and push Docker image ==="
az acr build \
  --registry $ACR_NAME \
  --image nordic-session-keeper:latest \
  .

# Get ACR credentials
ACR_SERVER=$(az acr show --name $ACR_NAME --query loginServer -o tsv)
ACR_USER=$(az acr credential show --name $ACR_NAME --query username -o tsv)
ACR_PASS=$(az acr credential show --name $ACR_NAME --query passwords[0].value -o tsv)

echo "=== 4. Create Storage Account and Blob Container ==="
az storage account create \
  --name $STORAGE_ACCOUNT_NAME \
  --resource-group $RESOURCE_GROUP \
  --location $LOCATION \
  --sku Standard_LRS

STORAGE_CONN_STR=$(az storage account show-connection-string \
  --name $STORAGE_ACCOUNT_NAME \
  --resource-group $RESOURCE_GROUP \
  --query connectionString -o tsv)

az storage container create \
  --name $CONTAINER_NAME \
  --connection-string "$STORAGE_CONN_STR"

echo "=== 5. Upload initial auth_state.json to Blob ==="
if [ -f "auth_state.json" ]; then
  az storage blob upload \
    --container-name $CONTAINER_NAME \
    --name $BLOB_NAME \
    --file auth_state.json \
    --connection-string "$STORAGE_CONN_STR" \
    --overwrite
  echo "auth_state.json uploaded."
else
  echo "[WARN] auth_state.json not found in current directory. Upload it manually before the first job run."
fi

echo "=== 6. Create Container Apps Environment ==="
az containerapp env create \
  --name $ENVIRONMENT_NAME \
  --resource-group $RESOURCE_GROUP \
  --location $LOCATION

echo "=== 7. Create Container App Job (every 25 minutes) ==="
# CRON: */25 * * * *  =  every 25 minutes, 24x7
az containerapp job create \
  --name $JOB_NAME \
  --resource-group $RESOURCE_GROUP \
  --environment $ENVIRONMENT_NAME \
  --trigger-type "Schedule" \
  --cron-expression "*/25 * * * *" \
  --replica-timeout 300 \
  --replica-retry-limit 1 \
  --image "$ACR_SERVER/nordic-session-keeper:latest" \
  --registry-server $ACR_SERVER \
  --registry-username $ACR_USER \
  --registry-password $ACR_PASS \
  --cpu 1.0 \
  --memory 2.0Gi \
  --env-vars \
    AZURE_STORAGE_CONNECTION_STRING="$STORAGE_CONN_STR" \
    BLOB_CONTAINER_NAME="$CONTAINER_NAME" \
    BLOB_NAME="$BLOB_NAME"

echo ""
echo "================================================================"
echo " Deployment complete."
echo " Job '$JOB_NAME' will run every 25 minutes automatically."
echo " Session is stored in: $STORAGE_ACCOUNT_NAME / $CONTAINER_NAME / $BLOB_NAME"
echo "================================================================"
