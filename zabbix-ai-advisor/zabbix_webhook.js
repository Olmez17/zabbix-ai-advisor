// Zabbix Webhook media type script
// Media type oluştururken "Parameters" sekmesinde aşağıdaki key'leri tanımla:
//   URL       -> http://<ai-advisor-host>:8001/webhook
//   Secret    -> (docker-compose'daki WEBHOOK_SECRET ile aynı değer, boş bırakılabilir)
//   Host      -> {HOST.NAME}
//   Severity  -> {EVENT.SEVERITY}
//   EventName -> {EVENT.NAME}
//   Message   -> {ALERT.MESSAGE}
//   Status    -> {EVENT.STATUS}

try {
    var params = JSON.parse(value);

    var req = new HttpRequest();
    req.addHeader('Content-Type: application/json');
    if (params.Secret) {
        req.addHeader('Authorization: Bearer ' + params.Secret);
    }

    var body = JSON.stringify({
        host: params.Host,
        severity: params.Severity,
        event_name: params.EventName,
        message: params.Message,
        status: params.Status
    });

    var resp = req.post(params.URL, body);

    if (req.getStatus() >= 300) {
        throw 'AI Advisor webhook isteği başarısız: HTTP ' + req.getStatus() + ' - ' + resp;
    }

    return 'OK';
} catch (error) {
    Zabbix.log(4, '[AI Advisor Webhook] ' + error);
    throw 'AI Advisor webhook hatası: ' + error;
}
