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

Please open an issue if you want to see other attributes and provide me with a sample of your sensor data by calling `http://192.168.x.y/data.json`.




## Examples

### Rounding and offset

Use [Template Sensors](https://www.home-assistant.io/integrations/template/) to round the values or to give them a offset.
```
sensor:
  - platform: template
    sensors:
      temperature:
        value_template: '{{ (states("sensor.feinstaubsensor_temperature") | float) | round(1) - 2}}'
        friendly_name: 'Temperature'
        unit_of_measurement: '°C'
```



## Calculate equivalent atmospheric pressure at sea level

To adjusted the atmospheric pressure to the equivalent sea level pressure you need to use the barometric formula which depends on the altitude and the current temperature.
In this example we use an altitude of 300m and receive the temperature from our sensor `sensor.feinstaubsensor_temperature`.


```
atmospheric_pressure:
      value_template: >-
        {% set temperature_gradient = 0.0065 %}
        {% set exponent = 0.03416 / temperature_gradient %}

        {% set altitude_meters = 300 %}
        {% set temperature_celsius = states('sensor.feinstaubsensor_temperature') | float %}
        {% set temperautre_at_sealevel_kelvin = temperature_celsius + (temperature_celsius * temperature_gradient) + 273.15 %}
        {% set air_pressure_hpa = (states('sensor.feinstaubsensor_pressure') | float / 100) | round(1) %}

        {{ (air_pressure_hpa / (1 - ((temperature_gradient * altitude_meters) / temperautre_at_sealevel_kelvin)) ** exponent) | round(1) }}
      friendly_name: 'Atmospheric pressure'
      unit_of_measurement: 'hPa'
```

