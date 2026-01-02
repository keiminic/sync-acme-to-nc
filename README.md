# Sync acme to nc

Automatically deploy SSL certificates to Netcup's WebHosting.

## Usage

### Using Container

```bash
podman run -it --rm \
  --ipc=host \
  -e NC_USER=ccp-id \
  -e NC_PASS=ccp-password \
  -e NC_2FA_SECRET=ccp-2fa-secret \
  -e NC_PRODUCT_ID=Hosting0000 \
  -e NC_DOMAIN=example.com \
  -v /path/to/fullchain/nginx/cert.pem:/data/cert.pem:ro \
  -v /path/to/keyfile/in/nginx/key.pem:/data/key.pem:ro \
  ghcr.io/keiminic/sync-acme-to-nc:latest
```

### Without Container

```bash
git clone https://github.com/keiminic/sync-acme-to-nc.git
cd sync-acme-to-nc
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install --with-deps chromium

EXPORT NC_USER=ccp-id
EXPORT NC_PASS=ccp-password
EXPORT NC_2FA_SECRET=ccp-2fa-secret
EXPORT NC_PRODUCT_ID=Hosting0000
EXPORT NC_DOMAIN=example.com

python main.py
```