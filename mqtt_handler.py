#! /usr/bin/python3

import paho.mqtt.client as mqtt
import threading


class mqtt_handler(threading.Thread):
    def __init__(self, broker_ip, topic_prefix):
        super().__init__()

        self.client = mqtt.Client()

        self.topic_prefix = topic_prefix

        self.topics = []

        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message

        self.client.connect(broker_ip, 1883, 60)

        self.name = 'GHBot MQTT'
        self.start()

    def get_topix_prefix(self):
        return self.topic_prefix

    def subscribe(self, topic, msg_recv_cb):
        print(f'mqtt_handler::topic: subscribe to {self.topic_prefix}{topic}')

        self.topics.append((self.topic_prefix + topic, msg_recv_cb))

        self.client.subscribe(self.topic_prefix + topic)

    def publish(self, topic, content, **attributes):
        print(f'mqtt_handler::topic: publish "{content}" to "{self.topic_prefix}{topic}"')

        persistent = False
        if 'persistent' in attributes:
            persistent = attributes['persistent']

        self.client.publish(self.topic_prefix + topic, content, retain=persistent)

    def on_connect(self, client, userdata, flags, rc):
        for topic in self.topics:
            print(f'mqtt_handler::topic: re-subscribe to {topic[0]}')

            self.client.subscribe(topic[0])

    def on_message(self, client, userdata, msg):
        # print(f'mqtt_handler::topic: received "{msg.payload}" in topic "{msg.topic}"')

        for topic in self.topics:
            cleaned = topic[0].replace('#','')

            if cleaned in msg.topic:
                topic[1](msg.topic, msg.payload.decode('utf-8'))

                return

        print(f'mqtt_handler::topic: no handler for topic "{msg.topic}"')

    def run(self):
        while True:
            print('mqtt_handler::run: looping')

            self.client.loop_forever()
