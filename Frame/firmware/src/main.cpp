#include <Arduino.h>
#include <WiFi.h>
#include <ArduinoJson.h>
#include "config.h"
#include "WifiManager.h"
#include "DhtSensor.h"
#include "MqttPublisher.h"
#include "MeshManager.h"

unsigned long lastReadTime = 0;

void onMeshMessageReceived(const String& payload) {
  MqttPublisher::forwardMeshPayload(payload);
}

void setup() {
  Serial.begin(115200);
  DhtSensor::begin();

  MeshManager::begin();

  if (IS_ROOT_NODE) {
    MeshManager::setOnMeshMessage(onMeshMessageReceived);

    // Fluxo classico de Wi-Fi, ja comprovado estavel nesta rede
    // antes do mesh - sem nenhuma "ponte" fragil no meio.
    WifiManager::connect();

    if (WiFi.status() == WL_CONNECTED) {
      Serial.print("Canal Wi-Fi deste no raiz (use em ESPNOW_CHANNEL dos sensores): ");
      Serial.println(WiFi.channel());
    }

    MqttPublisher::begin();
  }
}

void loop() {
  if (IS_ROOT_NODE) {
    if (WiFi.status() == WL_CONNECTED) {
      MqttPublisher::reconnectIfNeeded();
    }
  }

  if (lastReadTime == 0 || millis() - lastReadTime >= READ_INTERVAL_MS) {
    lastReadTime = millis();

    SensorReading reading = DhtSensor::read();

    if (!reading.valid) {
      Serial.println("Leitura invalida, nao enviado.");
      return;
    }

    if (IS_ROOT_NODE) {
      if (WiFi.status() == WL_CONNECTED) {
        MqttPublisher::publishReading(reading, NODE_ID);
      } else {
        Serial.println("Wi-Fi indisponivel, leitura descartada (root sem conexao).");
      }
    } else {
      StaticJsonDocument<128> doc;
      doc["id"] = NODE_ID;

      char tempStr[10];
      char umidStr[10];
      dtostrf(reading.temperature, 0, 3, tempStr);
      dtostrf(reading.humidity, 0, 3, umidStr);
      doc["temperatura"] = serialized(tempStr);
      doc["umidade"] = serialized(umidStr);

      String payload;
      serializeJson(doc, payload);

      MeshManager::broadcastReading(payload);
      Serial.println("Enviado via ESP-NOW: " + payload);
    }
  }
}
