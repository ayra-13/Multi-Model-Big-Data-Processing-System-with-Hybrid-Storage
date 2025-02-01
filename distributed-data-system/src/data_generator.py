# data_generator.py

import json
import random
import time
from PIL import Image, ImageDraw
import numpy as np
import cv2
import uuid
from confluent_kafka import Producer
import io
import base64
from faker import Faker
import logging
import os
from typing import Dict, Any, Union
import signal
import sys
from datetime import datetime
from flask import Flask, jsonify
import threading
from ratelimit import limits, sleep_and_retry
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST, make_wsgi_app, Counter, Summary, Gauge
from werkzeug.middleware.dispatcher import DispatcherMiddleware

app = Flask(__name__)
# Add Prometheus WSGI middleware to Flask app
app.wsgi_app = DispatcherMiddleware(app.wsgi_app, {
    '/metrics': make_wsgi_app()
})

class DataGenerator:
    def __init__(self, kafka_config: Dict[str, str]):
        self.producer = Producer({
            **kafka_config,
            'message.max.bytes': 10485760,  # 5MB max message size
            'compression.type': 'gzip'  # Enable compression
        })
        self.fake = Faker()
        self.running = True
        self.setup_logging()
        self.setup_signal_handlers()
        self.setup_metrics()
        
    def setup_metrics(self):
        """Initialize Prometheus metrics"""
        # Counters
        self.messages_generated = Counter('messages_generated_total', 
            'Total number of messages generated', ['type'])
        self.messages_sent = Counter('messages_sent_total', 
            'Total number of messages sent to Kafka', ['type'])
        self.message_errors = Counter('message_errors_total', 
            'Total number of errors in message generation/sending', ['type', 'error_type'])
        
        # Summaries
        self.message_size = Summary('message_size_bytes', 
            'Size of generated messages in bytes', ['type'])
        self.generation_time = Summary('message_generation_seconds', 
            'Time spent generating messages', ['type'])
        self.send_time = Summary('message_send_seconds', 
            'Time spent sending messages to Kafka', ['type'])
        
        # Gauges
        self.generation_rate = Gauge('message_generation_rate', 
            'Current rate of message generation per minute', ['type'])
        
        # Start Prometheus metrics server on port 8000
        # start_http_server(8000)
        
    def setup_logging(self):
        """Configure logging"""
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
        self.logger = logging.getLogger(__name__)
        
    def setup_signal_handlers(self):
        """Setup graceful shutdown handlers"""
        signal.signal(signal.SIGTERM, self.handle_shutdown)
        signal.signal(signal.SIGINT, self.handle_shutdown)
    
    def handle_shutdown(self, signum, frame):
        """Handle graceful shutdown"""
        self.logger.info("Received shutdown signal, cleaning up...")
        self.running = False
        
    def delivery_report(self, err, msg):
        """Kafka delivery report callback"""
        if err is not None:
            self.logger.error(f'Message delivery failed: {err}')
            self.message_errors.labels(type='kafka', error_type='delivery').inc()
        else:
            self.logger.debug(f'Message delivered to {msg.topic()} [{msg.partition()}]')
            self.messages_sent.labels(type=msg.topic().split('_')[0]).inc()

    @sleep_and_retry
    @limits(calls=100, period=60)  # Rate limit: 100 messages per minute
    def generate_text_data(self) -> Dict[str, Any]:
        """Generate synthetic text data with metadata"""
        start_time = time.time()
        text_types = ['article', 'comment', 'review']
        text_type = random.choice(text_types)
        
        try:
            data = {
                'id': str(uuid.uuid4()),
                'type': 'text',
                'text_type': text_type,
                'content': self.fake.text(max_nb_chars=1000),
                'metadata': {
                    'author': self.fake.name(),
                    'timestamp': datetime.now().isoformat(),
                    'language': random.choice(['en', 'es', 'fr', 'de']),
                    'sentiment': random.choice(['positive', 'negative', 'neutral']),
                    'tags': [self.fake.word() for _ in range(random.randint(1, 5))]
                }
            }
            self.messages_generated.labels(type='text').inc()
            self.generation_time.labels(type='text').observe(time.time() - start_time)
            return data
        except Exception as e:
            self.logger.error(f"Error generating text data: {str(e)}")
            self.message_errors.labels(type='text', error_type='generation').inc()
            raise

    @sleep_and_retry
    @limits(calls=50, period=60)  # Rate limit: 50 messages per minute
    def generate_image_data(self) -> Dict[str, Any]:
        """Generate synthetic image data with metadata"""
        start_time = time.time()
        try:
            # Create a smaller random colored image
            width, height = 240, 180  # Further reduced dimensions
            image = Image.new('RGB', (width, height), tuple(random.randint(0, 255) for _ in range(3)))
            
            # Add some random shapes
            draw = ImageDraw.Draw(image)
            for _ in range(random.randint(2, 4)):
                shape_color = tuple(random.randint(0, 255) for _ in range(3))
                x0 = random.randint(0, width - 10)
                y0 = random.randint(0, height - 10)
                x1 = x0 + random.randint(10, 30)  # Smaller shapes
                y1 = y0 + random.randint(10, 30)
                draw.rectangle([x0, y0, x1, y1], fill=shape_color)
            
            img_byte_arr = io.BytesIO()
            image.save(img_byte_arr, format='PNG', optimize=True, compression_level=9)
            img_byte_arr = img_byte_arr.getvalue()
            
            size_mb = len(img_byte_arr) / (1024 * 1024)
            if size_mb > 5:
                self.logger.warning(f"Generated image too large ({size_mb:.2f}MB), skipping")
                self.message_errors.labels(type='image', error_type='size_limit').inc()
                return None
            
            data = {
                'id': str(uuid.uuid4()),
                'type': 'image',
                'content': base64.b64encode(img_byte_arr).decode('utf-8'),
                'metadata': {
                    'timestamp': datetime.now().isoformat(),
                    'width': width,
                    'height': height,
                    'format': 'PNG',
                    'size': len(img_byte_arr),
                    'size_mb': f"{size_mb:.2f}",
                    'tags': [self.fake.word() for _ in range(random.randint(1, 3))]
                }
            }
            self.messages_generated.labels(type='image').inc()
            self.generation_time.labels(type='image').observe(time.time() - start_time)
            return data
        except Exception as e:
            self.logger.error(f"Error generating image data: {str(e)}")
            self.message_errors.labels(type='image', error_type='generation').inc()
            raise

    def send_to_kafka(self, data: Dict[str, Any], topic: str):
        """Send data to Kafka with error handling"""
        start_time = time.time()
        try:
            if data is None:
                return
            
            message = json.dumps(data).encode('utf-8')
            message_size_mb = len(message) / (1024 * 1024)
        
            if message_size_mb > 8:  # Changed to 9MB to allow some overhead
                self.logger.warning(f"Message too large ({message_size_mb:.2f}MB), skipping")
                self.message_errors.labels(type=topic.split('_')[0], 
                                        error_type='size_limit').inc()
                return
                
            self.message_size.labels(type=topic.split('_')[0]).observe(len(message))
            self.producer.produce(
                topic,
                value=message,
                callback=self.delivery_report
            )
            self.producer.poll(0)
            
            self.send_time.labels(type=topic.split('_')[0]).observe(time.time() - start_time)
            
        except Exception as e:
            self.logger.error(f"Error sending data to Kafka: {str(e)}")
            self.message_errors.labels(type=topic.split('_')[0], 
                                    error_type='kafka_send').inc()
            raise

    def run(self):
        """Main generation loop"""
        self.logger.info("Starting data generation...")
        
        text_count = 0
        image_count = 0
        last_minute = time.time()
        
        try:
            while self.running:
                try:
                    # Generate and send text data
                    text_data = self.generate_text_data()
                    self.send_to_kafka(text_data, 'text_data_topic')
                    text_count += 1
                    
                    # Generate and send image data
                    image_data = self.generate_image_data()
                    self.send_to_kafka(image_data, 'image_data_topic')
                    image_count += 1
                    
                    # Update generation rates every minute
                    current_time = time.time()
                    if current_time - last_minute >= 60:
                        self.generation_rate.labels(type='text').set(text_count)
                        self.generation_rate.labels(type='image').set(image_count)
                        text_count = 0
                        image_count = 0
                        last_minute = current_time
                    
                    self.producer.flush()
                    time.sleep(1)
                    
                except Exception as e:
                    self.logger.error(f"Error in generation loop: {str(e)}")
                    time.sleep(5)
                    
        except KeyboardInterrupt:
            self.logger.info("Received keyboard interrupt, shutting down...")
        finally:
            self.cleanup()
            
    def cleanup(self):
        """Cleanup resources"""
        try:
            if hasattr(self, 'producer'):
                self.producer.flush()
                self.producer.close()
            self.logger.info("Successfully cleaned up resources")
        except Exception as e:
            self.logger.error(f"Error during cleanup: {str(e)}")

# Health check endpoint
@app.route('/health')
def health_check():
    return jsonify({"status": "healthy"}), 200

if __name__ == "__main__":
    kafka_config = {
    'bootstrap.servers': os.getenv('KAFKA_BOOTSTRAP_SERVERS', 'kafka:29092'),  # Changed to 29092
    'client.id': 'data_generator',
    'message.max.bytes': 10485760,
    'compression.type': 'gzip',
    'batch.size': 64000,
    'linger.ms': 10,
    'acks': 'all',
    'retries': 5,
    'retry.backoff.ms': 500
}
    
    generator = DataGenerator(kafka_config)
    
    # Start health check server
    health_thread = threading.Thread(target=lambda: app.run(host='0.0.0.0', port=8001))
    health_thread.daemon = True
    health_thread.start()
    
    # Start generation
    generator.run()