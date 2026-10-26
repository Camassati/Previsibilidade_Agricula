#include "WifiManager.h"
#include <Arduino.h>
#include <WiFi.h>
#include <time.h>
#include "config.h"

void WifiManager::connect() {

  WiFi.mode(WIFI_STA);

  Serial.println();
  Serial.println("=== TESTE DE WI-FI ===");
  Serial.print("SSID configurado: ");
  Serial.println(WIFI_SSID);

  Serial.println("Procurando redes Wi-Fi...");

  int networks = WiFi.scanNetworks();

  if (networks == 0) {
    Serial.println("Nenhuma rede Wi-Fi encontrada.");
  } else {

    Serial.printf("%d redes encontradas:\n", networks);

    for (int i = 0; i < networks; i++) {

      Serial.printf(
        "%d - %s | RSSI: %d dBm | Canal: %d\n",
        i + 1,
        WiFi.SSID(i).c_str(),
        WiFi.RSSI(i),
        WiFi.channel(i)
      );
    }
  }

  WiFi.scanDelete();

  Serial.println();
  Serial.print("Tentando conectar em: ");
  Serial.println(WIFI_SSID);

  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

  unsigned long start = millis();

  while (WiFi.status() != WL_CONNECTED && millis() - start < 15000) {
    delay(500);

    Serial.print(".");
  }

  Serial.println();

  if (WiFi.status() == WL_CONNECTED) {

    Serial.println("================================");
    Serial.println("WI-FI CONECTADO!");
    Serial.print("SSID: ");
    Serial.println(WiFi.SSID());

    Serial.print("IP: ");
    Serial.println(WiFi.localIP());

    Serial.print("Canal: ");
    Serial.println(WiFi.channel());

    Serial.print("RSSI: ");
    Serial.print(WiFi.RSSI());
    Serial.println(" dBm");

    Serial.println("================================");

    configTime(
      GMT_OFFSET_SEC,
      DAYLIGHT_OFFSET_SEC,
      NTP_SERVER
    );

    Serial.print("Sincronizando hora via NTP");

    struct tm timeinfo;

    unsigned long ntpStart = millis();

    while (
      !getLocalTime(&timeinfo, 1000) &&
      millis() - ntpStart < 10000
    ) {
      Serial.print(".");
    }

    if (getLocalTime(&timeinfo)) {

      char buf[25];

      strftime(
        buf,
        sizeof(buf),
        "%Y-%m-%d %H:%M:%S",
        &timeinfo
      );

      Serial.println();
      Serial.print("Hora sincronizada: ");
      Serial.println(buf);

    } else {

      Serial.println();
      Serial.println("Falha ao sincronizar hora via NTP.");
    }

  } else {

    Serial.println("================================");
    Serial.println("FALHA AO CONECTAR AO WI-FI");
    Serial.print("Status Wi-Fi: ");
    Serial.println(WiFi.status());
    Serial.println("================================");
  }
}

bool WifiManager::isConnected() {
  return WiFi.status() == WL_CONNECTED;
}