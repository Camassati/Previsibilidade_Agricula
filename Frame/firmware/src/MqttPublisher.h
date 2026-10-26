#pragma once
#include "DhtSensor.h"
#include <Arduino.h>

class MqttPublisher {
public:
  static void begin();
  static void reconnectIfNeeded();

  // Publica uma leitura feita pelo proprio no raiz.
  static void publishReading(const SensorReading& reading, const char* nodeId);

  // So usado pelo no raiz: recebe um payload JSON vindo de outro no
  // via mesh (sem hora real, ja que nos-sensores nao tem NTP),
  // substitui/adiciona a hora certa e publica no broker.
  static void forwardMeshPayload(const String& rawJson);
};
