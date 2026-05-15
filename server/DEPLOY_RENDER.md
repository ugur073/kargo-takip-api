# Render Deploy Notlari

Bu dosya v2.12 HTTPS onay linki akisini bilgisayar kapali olsa bile calistirmak icin kullanilir.

## 1. Render hesabi

1. https://render.com adresinden hesap ac.
2. GitHub baglantisi istenirse projeyi GitHub'a yukle veya Render dashboard uzerinden repo bagla.
3. `render.yaml` dosyasi Blueprint olarak algilanir.

## 2. Ortam degiskenleri

Render servis ayarlarinda su degerleri gir:

```text
BASE_PUBLIC_URL=https://kargo-takip-api.onrender.com
DEVICE_API_TOKEN=uzun-guclu-bir-token
WHATSAPP_VERIFY_TOKEN=uzun-guclu-bir-verify-token
ALLOW_DEV_SIMULATOR=false
DB_PATH=/var/data/kargo.db
```

Servis adini degistirirsen Render URL de degisir. O durumda `BASE_PUBLIC_URL` degerini yeni HTTPS adrese cek.

## 3. Android ayari

Uygulamada:

```text
Ayarlar > Sunucu ve Webhook
Sunucu URL: Render'in verdigi HTTPS adres
Cihaz API Token: DEVICE_API_TOKEN ile ayni token
```

`Saglik kontrolu` basarili donerse telefon PC'ye bagli olmadan sunucuya ulasabilir.

## 4. Kalici veri

`render.yaml` icinde `/var/data` disk mount edilir ve SQLite veritabani `/var/data/kargo.db` altinda tutulur. Disk olmadan SQLite dosyasi deploy/restart sonrasi kaybolabilir.

## 5. Beklenen akış

1. Telefon uygulamasi musteri kartini `POST /api/tasks/upsert` ile Render sunucusuna gonderir.
2. Render `confirmation_url` dondurur.
3. Android WhatsApp mesajina bu linki ekler.
4. Musteri linke girip teslimat kodunu yazar.
5. Telefon uygulamasi `GET /api/tasks/updates` ile kodlari ceker.
