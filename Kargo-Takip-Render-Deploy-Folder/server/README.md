# Kargo HTTPS Onay ve WhatsApp Webhook Sunucusu

Bu klasor, Android uygulamasinin teslimat kodlarini guvenilir sekilde almasi icin yerel FastAPI + SQLite sunucusunu icerir.

Bilgisayar kapali kalacaksa Render deploy akisini kullan. Ayrintili notlar:

- `server/DEPLOY_RENDER.md`
- `render.yaml`

## Kurulum

```powershell
powershell -ExecutionPolicy Bypass -File .\server\scripts\setup-server.ps1
```

Script sunlari yapar:

- `server\.venv` Python sanal ortamini olusturur.
- FastAPI, Uvicorn, pytest ve QR uretimi bagimliliklarini kurar.
- `.env.example` dosyasindan `.env` olusturur.
- `cloudflared.exe` indirir ve `server\.tools` klasorunu kullanici `PATH` degerine ekler.

## Calistirma

```powershell
powershell -ExecutionPolicy Bypass -File .\server\scripts\start-server.ps1
powershell -ExecutionPolicy Bypass -File .\server\scripts\start-tunnel.ps1
```

Yerel saglik kontrolu:

```powershell
Invoke-RestMethod http://127.0.0.1:8787/health
```

Quick tunnel URL'sini aldiktan sonra `.env` icindeki `BASE_PUBLIC_URL` degerini bu HTTPS adresine cek. Android uygulamasinda `Ayarlar > Sunucu ve Webhook` alanina ayni HTTPS adresini ve `DEVICE_API_TOKEN` degerini gir.

## Render / Bulut Modu

Render ile deploy ederken:

```text
BASE_PUBLIC_URL=https://kargo-takip-api.onrender.com
DEVICE_API_TOKEN=uzun-guclu-bir-token
ALLOW_DEV_SIMULATOR=false
DB_PATH=/var/data/kargo.db
```

Android uygulamada `Ayarlar > Sunucu ve Webhook` alanina Render'in verdigi HTTPS adresi ve ayni `DEVICE_API_TOKEN` girilir.

## WhatsApp Webhook

Gercek Meta bilgileri gelene kadar `ALLOW_DEV_SIMULATOR=true` ile simulator kullanilabilir. Production icin `.env` icinde su alanlari doldur:

- `BASE_PUBLIC_URL`
- `DEVICE_API_TOKEN`
- `WHATSAPP_VERIFY_TOKEN`
- `META_APP_SECRET`
Meta webhook dogrulama adresi:

```text
GET /webhooks/whatsapp
POST /webhooks/whatsapp
```

Webhook POST akisinda `X-Hub-Signature-256` imzasi `META_APP_SECRET` verilmisse zorunlu olarak dogrulanir.

## Test

```powershell
.\server\.venv\Scripts\python.exe -m pytest .\server\smoke_test.py -q
```

Testler saglik kontrolunu, HTTPS onay formunu, guncelleme cekmeyi, WhatsApp verify akisini ve duplicate message id korumasini dogrular.
