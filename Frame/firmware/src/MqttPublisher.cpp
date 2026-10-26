#include "MqttPublisher.h"
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>
#include <time.h>
#include "config.h"

static WiFiClientSecure wifiClient;
static PubSubClient mqttClient(wifiClient);

static String currentHora() {
  struct tm timeinfo;
  char buf[25];
  if (getLocalTime(&timeinfo)) {
    strftime(buf, sizeof(buf), "%Y-%m-%d %H:%M:%S", &timeinfo);
    return String(buf);
  }
  return "hora_indisponivel";
}

void MqttPublisher::begin() {
  wifiClient.setInsecure();
  mqttClient.setServer(MQTT_BROKER, MQTT_PORT);
  mqttClient.setSocketTimeout(60);
  mqttClient.setKeepAlive(60);
}

void MqttPublisher::reconnectIfNeeded() {
  while (!mqttClient.connected()) {
    Serial.print("Conectando ao broker MQTT... Broker: ");
    Serial.print(MQTT_BROKER);
    Serial.print(":");
    Serial.println(MQTT_PORT);

    if (mqttClient.connect(MQTT_CLIENT_ID, MQTT_USERNAME, MQTT_PASSWORD)) {
      Serial.println("MQTT conectado!");
    } else {
      Serial.print("Falha ao conectar, rc=");
      Serial.print(mqttClient.state());
      Serial.println(" tentando novamente em 5s");
      delay(5000);
    }
  }
  mqttClient.loop();
}

void MqttPublisher::publishReading(const SensorReading& reading, const char* nodeId) {
  if (!reading.valid) {
    Serial.println("Leitura invalida, nao publicado.");
    return;
  }

  char tempStr[10];
  char umidStr[10];
  dtostrf(reading.temperature, 0, 3, tempStr);
  dtostrf(reading.humidity, 0, 3, umidStr);

  StaticJsonDocument<200> doc;
  doc["id"] = nodeId;
  doc["hora"] = currentHora();
  doc["temperatura"] = serialized(tempStr);
  doc["umidade"] = serialized(umidStr);

  char payload[200];
  size_t len = serializeJson(doc, payload);

  if (mqttClient.publish(MQTT_TOPIC, payload, len)) {
    Serial.println("Publicado (local): " + String(payload));
  } else {
    Serial.println("Falha ao publicar no MQTT.");
  }
}

void MqttPublisher::forwardMeshPayload(const String& rawJson) {
  StaticJsonDocument<200> doc;
  DeserializationError err = deserializeJson(doc, rawJson);

  if (err) {
    Serial.print("Payload da mesh invalido, descartado: ");
    Serial.println(err.c_str());
    return;
  }

  // Substitui/adiciona a hora, ja que quem enviou (no sensor, sem
  // Wi-Fi/NTP) nao tem como saber a hora real.
  doc["hora"] = currentHora();

  char payload[200];
  size_t len = serializeJson(doc, payload);

  if (mqttClient.publish(MQTT_TOPIC, payload, len)) {
    Serial.println("Publicado (via mesh): " + String(payload));
  } else {
    Serial.println("Falha ao publicar no MQTT (mensagem da mesh).");
  }
}
