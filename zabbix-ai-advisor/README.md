# Zabbix AI Advisor

Zabbix'ten gelen alarm/problem bilgisini alıp Google Gemini API'ye gönderen,
aldığı öneriyi basit bir web dashboard'da gösteren küçük bir FastAPI servisi.

Gemini API, **kredi kartı gerektirmeyen, süresiz bir ücretsiz katman** sunuyor
(Flash modelleri, günde ~1500 istek limiti) — bu proje ölçeğinde tamamen
yeterli ve bedava.

## Mimari

```
Zabbix (trigger action) --webhook--> FastAPI /webhook --> Gemini API
                                              |
                                              v
                                         SQLite (alerts.db)
                                              |
                                              v
                                      Dashboard (GET /)
```

## 1) Klasörü sunucuna taşı

Bu klasörün tamamını `~/zabbix/zabbix-ai-advisor/` altına kopyala
(yani `~/zabbix/docker-compose.yml` ile aynı seviyede bir alt klasör olsun).

```bash
ls ~/zabbix
# docker-compose.yml  agent2-nginx.conf  zabbix-ai-advisor/
```

## 2) docker-compose.yml'e servisi ekle

`docker-compose.snippet.yml` içindeki bloğu, `~/zabbix/docker-compose.yml`
dosyandaki `services:` altına ekle (nano ile aç, mysql/zabbix-server'ın
yanına yapıştır). `volumes:` bölümüne de `ai-advisor-data:` satırını ekle.

`GEMINI_API_KEY` alanına kendi Gemini API key'ini yaz. Almak için:

1. **aistudio.google.com/apikey** adresine git
2. Google hesabınla giriş yap (kredi kartı istemez)
3. **"Create API key"** butonuna bas, key'i kopyala (`AIzaSy...` ile başlar)

`WEBHOOK_SECRET` alanına kendi seçtiğin bir parola yaz — bunu birazdan
Zabbix tarafında da aynı şekilde kullanacağız.

## 3) Build & çalıştır

```bash
cd ~/zabbix
docker compose up -d --build ai-advisor
docker logs zabbix-ai-advisor
```

`docker logs` çıktısında hata olmamalı. Test et:

```bash
curl http://localhost:8001/health
# {"status":"ok","ai_enabled":true}
```

Dashboard'u tarayıcıda aç: `http://192.168.43.128:8001`

## 4) Zabbix'te Webhook Media Type oluştur

**Alerts → Media types → Create media type**

- **Name:** `AI Advisor`
- **Type:** `Webhook`
- **Parameters** sekmesinde şu key/value çiftlerini ekle:

| Name | Value |
|---|---|
| URL | `http://ai-advisor:8001/webhook` |
| Secret | `docker-compose'daki WEBHOOK_SECRET ile aynı değer` |
| Host | `{HOST.NAME}` |
| Severity | `{EVENT.SEVERITY}` |
| EventName | `{EVENT.NAME}` |
| Message | `{ALERT.MESSAGE}` |
| Status | `{EVENT.STATUS}` |

> `URL` alanında `ai-advisor` yazdık çünkü aynı docker-compose network'ündeki
> servis adıyla birbirlerine ulaşabilirler (zabbix-server konteyneri bu
> servise container ismiyle erişir). Eğer zabbix-server `network_mode: host`
> kullanmıyorsa bu şekilde çalışır; agent'ın host modunda olması bunu etkilemez,
> çünkü bu istek zabbix-server konteynerinden çıkıyor.

- **Script** kutusuna `zabbix_webhook.js` dosyasının içeriğini yapıştır.
- **Enabled** olarak kaydet.

## 5) Kullanıcına bu medyayı ekle

**Users → Users → [kendi kullanıcın] → Media → Add**

- **Type:** `AI Advisor`
- **Send to:** herhangi bir değer yazabilirsin (webhook için genelde önemsiz, "-" gibi bir şey)
- **Status:** Enabled

## 6) Trigger action'a bu medyayı da ekle

**Alerts → Actions → Trigger actions → "Report problems to Zabbix administrators"**
(veya kendi action'ın) → **Operations** → mevcut operasyonu düzenle →
**Send only to** alanına `AI Advisor`'ı da ekle (email'in yanında, ikisi
birlikte tetiklenebilir).

## 7) Test

```bash
docker stop weather-app-web-1
```

Birkaç dakika bekle, sonra `http://192.168.43.128:8001` adresine git —
yeni bir kart, host adı, mesaj ve altında Claude'un ürettiği teşhis/öneri
görünmeli.

```bash
docker start weather-app-web-1
```

## Notlar

- `GEMINI_MODEL` olarak `gemini-2.5-flash` kullanılıyor — ücretsiz katmanda
  günde ~1500 istek hakkın var, bu proje için fazlasıyla yeterli.
- Ücretsiz katmanda gönderdiğin prompt/cevaplar Google tarafından model
  geliştirme amacıyla kullanılabilir (Google'ın belgelediği bir durum).
  Hassas/gizli veri içeren alarm mesajları gönderiyorsan bunu bil.
- Dashboard 30 saniyede bir otomatik yenilenir.
- Veriler `ai-advisor-data` adlı Docker volume'ünde (`/data/alerts.db`)
  kalıcı olarak saklanır.
- Şu an "basic" kapsamda: sadece gelen alarm metnini AI'ya gönderiyor.
  İleride istersen ilgili metrik geçmişini (CPU/memory grafiği vb.)
  de prompt'a ekleyip daha isabetli öneriler alacak şekilde genişletebiliriz.
