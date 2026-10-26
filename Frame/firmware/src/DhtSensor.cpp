#include "DhtSensor.h"
#include <DHT.h>
#include "config.h"

static DHT dht(DHTPIN, DHTTYPE);

void DhtSensor::begin() {
  dht.begin();
}

SensorReading DhtSensor::read() {
  SensorReading reading;
  reading.humidity = dht.readHumidity();
  reading.temperature = dht.readTemperature();
  reading.valid = !isnan(reading.humidity) && !isnan(reading.temperature);

  // Validação de faixa (consistente com o CT01 do TCC: -20°C a 60°C)
  if (reading.valid) {
    if (reading.temperature < -20.0 || reading.temperature > 60.0) {
      reading.valid = false;
    }
    if (reading.humidity < 0.0 || reading.humidity > 100.0) {
      reading.valid = false;
    }
  }

  return reading;
}
