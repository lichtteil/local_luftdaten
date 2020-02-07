# Custom Luftdaten component for Home Assistant

## Installation
- Create the directory `custom_components/local_luftdaten/` in your Home Assitant config directory (e.g. `~/.homeassistant/custom_components/local_luftdaten/`)
- Download and unzip or clone this repository and move its content to your `local_luftdaten` directory

## Configuration
Create a new sensor entry in your `configuration.yaml` and adjust the host name or the ip address.

```yaml
sensor:
  - platform: local_luftdaten
    host: 192.168.0.123
    name: Feinstaubsensor
    monitored_conditions:
      - SDS_P1
      - SDS_P2
      - temperature
      - humidity
```

At the moment following sensor data can be read:

- SDS_P1
- SDS_P2
- temperature
- humidity
- signal
- BME280_temperature
- BME280_humidity
- BME280_pressure

Sensor type `signal` gives the wifi signal strength of the sensor device.


## Customizing

Use [Template Sensors](https://www.home-assistant.io/integrations/template/) to round the values or to give them a offset.
```
sensor:
  - platform: template
    sensors:
      air_pressure:
        value_template: '{{ (states("sensor.feinstaubsensor_pressure") | float / 100) | round(1) }}'
        friendly_name: 'Luftdruck'
        unit_of_measurement: 'hPa'
```


Please open an issue if you want to see other attributes and provide me with a sample of your sensor data by calling `http://192.168.x.y/data.json`.
