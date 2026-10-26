#pragma once

struct SensorReading {
  float temperature;
  float humidity;
  bool valid;
};

class DhtSensor {
public:
  static void begin();
  static SensorReading read();
};
