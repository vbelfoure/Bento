import json
import time
import asyncio
from machine import Pin, I2C, ADC
import network
from umqtt.simple import MQTTClient
import framebuf
import freesans20
import writer
import ssd1306
import progress_bar
from ssd1306 import SSD1306_I2C
from Sensors import Sensors
from SplashLogos import Logos
import sys

# Global variables and configurations
running = True
connection_state = True
incoming_message = None  # Stores received MQTT message

# GPIO Pin Definitions
debug_pin = 15  # main.py will exit if debug_pin is connected to GND
fan_pin = 22    # GPIO pin of fan relay signal

# LED and Fan Setup
debug_pin = Pin(debug_pin, Pin.IN, Pin.PULL_DOWN)
fan_relay = Pin(fan_pin, Pin.OUT)

# Temperature sensor setup
temp_sensor = ADC(4)

class DisplayService:
    def __init__(self, connection_state, config):
        self.I2C = I2C(config['I2Channel'], sda=Pin(config['sda_pin']), scl=Pin(config['scl_pin']), freq=config['freq'])
        self.width = config['width']
        self.config = config
        self.availableSplashLogo = Logos()
        self.height = config['height']
        self.defaultSplashLogo = config['splashLogo']
        self.display = SSD1306_I2C(self.width, self.height, self.I2C)
        self.displayFormat = writer.Writer(self.display, freesans20)
        self._connectionState = connection_state

    async def display_task(self, sensors):
        global running
        voc = sensors.voc
        count = 0
        seconds = 0
        voc_level_avg = 0
        voc_level_sum = 0
        show_temp = True
        
        while running:
            if not self._connectionState:
                await self.display_progress_bar(self.config)
            else:
                if count == 0:
                    self.clear_display()
                if count <= 49:
                    count += 1
                    voc_level_sum += sensors.airQualityIndex
                    voc_level_avg = voc_level_sum / count
                    self.displayFormat.set_textpos(0,0)
                    self.displayFormat.printstring(f"VOC: {round(voc_level_avg, 1)}")
                    self.display.show()
                    
                    temp = sensors.temperature
                    adc_value = temp_sensor.read_u16()
                    voltage = adc_value * (3.3 / 65535.0)
                    temp = 27 - (voltage - 0.706) / 0.001721
                    if seconds <= 5:
                        if show_temp:
                            self.displayFormat.set_textpos(0, 21)
                            self.displayFormat.printstring(f"TEMP: {round(temp, 1)}C")
                            self.display.show()
                            await asyncio.sleep(0.5)
                            seconds += 1
                    else:
                        seconds = 0
                        show_temp = not show_temp
                        self.display.show()
                else:
                    count = 1  
                    voc_level_sum = voc_level_avg
                    self.display.show()

                if voc_level_avg >= voc.threshold:
                    fan_relay.on()  # Relay pin high
                    self.displayFormat.set_textpos(0,42)
                    self.displayFormat.printstring("FAN: ON ")
                else:
                    fan_relay.off()  # Relay pin low
                    self.displayFormat.set_textpos(0,42) 
                    self.displayFormat.printstring("FAN: OFF ")
            await asyncio.sleep(1)  # Delay for display update rate
        
    def clear_display(self):
        self.display.invert(0)
        self.display.fill(0)
        self.display.show()

def load_config():
    with open('config.json') as config_file:
        return json.load(config_file)

async def connect_to_wifi(wifi_config):
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    if not wlan.isconnected():
        wlan.connect(wifi_config['ssid'], wifi_config['password'])
        wait_time = 0
        while not wlan.isconnected() and wait_time < 20:
            await asyncio.sleep(1)
            wait_time += 1
    print("Network connected:", wlan.ifconfig())
    return wlan

def mqtt_callback(topic, msg):
    global incoming_message
    incoming_message = (topic.decode(), msg.decode())
    print(f"Received message on {topic.decode()}: {msg.decode()}")

async def connect_to_mqtt(mqtt_config):
    client = MQTTClient(mqtt_config['client_id'], mqtt_config['broker'], port=mqtt_config['port'])
    client.set_callback(mqtt_callback)
    client.connect()
    client.subscribe(mqtt_config['topic_in'])
    print(f"Connected to MQTT Broker {mqtt_config['broker']} and subscribed to topic {mqtt_config['topic_in']}")
    return client

async def mqtt_task(client):
    global running
    while running:
        client.check_msg()  # Non-blocking message check
        await asyncio.sleep(0.1)  # Small delay to reduce CPU load

async def ensure_wifi_connected(wlan, wifi_config):
    if not wlan.isconnected():
        print("Reconnecting to WiFi...")
        wlan.connect(wifi_config['ssid'], wifi_config['password'])
        while not wlan.isconnected():
            await asyncio.sleep(1)

async def shutdown(client, display):
    global running
    running = False  # Signal all tasks to stop
    await asyncio.sleep(1)  # Allow tasks to clean up
    client.disconnect()
    display.clear_display()
    print("Shutdown complete.")

async def main():
    global running

    if debug_pin.value() == 1:
        print("Exiting to debug mode.")
        sys.exit()

    config = load_config()
    wifi_config = config['wifi']
    mqtt_config = config['mqtt']

    # Connect to WiFi
    wlan = await connect_to_wifi(wifi_config)

    # Connect to MQTT broker
    client = await connect_to_mqtt(mqtt_config)

    # Initialize sensors and display service
    sensors = Sensors(config['sensors'])
    display = DisplayService(wlan.isconnected(), config['display'])

    # Start asyncio tasks for MQTT and display updates
    try:
        await asyncio.gather(
            mqtt_task(client),
            display.display_task(sensors),
            ensure_wifi_connected(wlan, wifi_config)
        )
    except KeyboardInterrupt:
        print("Shutting down...")
        await shutdown(client, display)
    finally:
        print("Disconnected from MQTT and cleared display.")

# Run the main function
asyncio.run(main())
