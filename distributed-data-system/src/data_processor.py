# data_processor.py

from confluent_kafka import Consumer, Producer, KafkaError
from elasticsearch import Elasticsearch
from pymongo import MongoClient
from cassandra.cluster import Cluster
from cassandra.policies import RetryPolicy
import boto3
import json
import logging
import base64
from typing import Dict, Any, Union
import os
from datetime import datetime
import uuid
from PIL import Image
import io
import signal
import sys
import time
from tenacity import retry, stop_after_attempt, wait_exponential
from flask import Flask, jsonify
from bson import ObjectId

app = Flask(__name__)

class DataProcessor:
    def __init__(self):
        self.kafka_config = {
            'bootstrap.servers': os.getenv('KAFKA_BOOTSTRAP_SERVERS'),
            'group.id': 'data_processor_group',
            'auto.offset.reset': 'earliest',
            'message.max.bytes': 10485760,  # 5MB max message size
            'max.partition.fetch.bytes': 10485760,  # 5MB max fetch size
            'fetch.message.max.bytes': 10485760  # 5MB max fetch
        }
        self.running = True
        self.setup_logging()
        self.setup_signal_handlers()
        self.init_connections()
        
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

    @retry(stop=stop_after_attempt(5), wait=wait_exponential(multiplier=2, min=4, max=60))
    def init_cassandra_connection(self):
        """Initialize Cassandra connection with retries"""
        try:
            self.logger.info("Attempting to connect to Cassandra...")
            self.cassandra_cluster = Cluster(
                [os.getenv('CASSANDRA_HOSTS')],
                protocol_version=4,
                port=9042,
                connect_timeout=20
            )
            self.cassandra_session = self.cassandra_cluster.connect()
            self.logger.info("Successfully connected to Cassandra")
            return True
        except Exception as e:
            self.logger.error(f"Failed to connect to Cassandra: {str(e)}")
            raise

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
    def init_connections(self):
        """Initialize connections to various services with retry logic"""
        try:
            # Kafka
            self.consumer = Consumer(self.kafka_config)
            self.producer = Producer(self.kafka_config)
            
            # MongoDB - for metadata
            self.mongo_client = MongoClient(
                os.getenv('MONGODB_URI'),
                serverSelectionTimeoutMS=5000
            )
            # Test connection
            self.mongo_client.server_info()
            self.db = self.mongo_client['data_processing']
            
            # Elasticsearch - for search
            self.es_client = Elasticsearch(
                [os.getenv('ELASTICSEARCH_URL')],
                retry_on_timeout=True,
                max_retries=3
            )
            
            # S3 - for raw data storage
            self.s3_client = boto3.client(
                's3',
                aws_access_key_id=os.getenv('S3_ACCESS_KEY'),
                aws_secret_access_key=os.getenv('S3_SECRET_KEY'),
                endpoint_url=os.getenv('S3_ENDPOINT_URL')
            )
            self.bucket_name = os.getenv('S3_BUCKET', 'data-storage')
            
            # Ensure S3 bucket exists
            self.ensure_bucket_exists()
            
            # Cassandra - for time series data
            self.init_cassandra_connection()
            self.init_cassandra_schema()

            self.logger.info("Successfully initialized all connections")
            
        except Exception as e:
            self.logger.error(f"Failed to initialize connections: {str(e)}")
            raise
    
    def ensure_bucket_exists(self):
        """Ensure S3 bucket exists in an idempotent way"""
        try:
            self.s3_client.head_bucket(Bucket=self.bucket_name)
        except:
            try:
                self.s3_client.create_bucket(Bucket=self.bucket_name)
                self.logger.info(f"Created S3 bucket: {self.bucket_name}")
            except Exception as e:
                if "BucketAlreadyOwnedByYou" not in str(e):
                    raise
    
    def init_cassandra_schema(self):
        """Initialize Cassandra keyspace and tables"""
        try:
        # Create keyspace
            self.cassandra_session.execute("""
                CREATE KEYSPACE IF NOT EXISTS data_processing
                WITH replication = {'class': 'SimpleStrategy', 'replication_factor': '1'}
            """)
            
            # Create tables for different data types
            self.cassandra_session.execute("""
                CREATE TABLE IF NOT EXISTS data_processing.processing_metrics (
                    id uuid,
                    timestamp timestamp,
                    data_type text,
                    processing_time double,
                    success boolean,
                    PRIMARY KEY (data_type, timestamp, id)
                )
            """)
            self.logger.info("Successfully initialized Cassandra schema")
        except Exception as e:
            self.logger.error(f"Error initializing Cassandra schema: {str(e)}")
            raise

    def cleanup(self):
        """Cleanup connections"""
        try:
            if hasattr(self, 'consumer'):
                self.consumer.close()
            if hasattr(self, 'mongo_client'):
                self.mongo_client.close()
            if hasattr(self, 'es_client'):
                self.es_client.close()
            if hasattr(self, 'cassandra_session'):
                self.cassandra_session.close()
            self.logger.info("Successfully cleaned up all connections")
        except Exception as e:
            self.logger.error(f"Error during cleanup: {str(e)}")

    def _serialize_mongodb_doc(self, doc: Dict[str, Any]) -> Dict[str, Any]:
        """Convert MongoDB document to serializable dictionary"""
        serialized = {}
        for key, value in doc.items():
            if key == '_id':  # Skip MongoDB's _id field
                continue
            elif isinstance(value, ObjectId):
                serialized[key] = str(value)
            elif isinstance(value, dict):
                serialized[key] = self._serialize_mongodb_doc(value)
            elif isinstance(value, list):
                serialized[key] = [
                    self._serialize_mongodb_doc(item) if isinstance(item, dict) else str(item) if isinstance(item, ObjectId) else item
                    for item in value
                ]
            else:
                serialized[key] = value
        return serialized

    def process_text(self, data: Dict[str, Any]) -> None:
        """Process text data and store for analytics"""
        start_time = datetime.now()
        content = data['content']
        
        try:
            # Store raw text in S3
            s3_key = f"text/{data['id']}.txt"
            self.s3_client.put_object(
                Bucket=self.bucket_name,
                Key=s3_key,
                Body=content.encode('utf-8')
            )
            
            # Store metadata in MongoDB
            metadata = {
                **data['metadata'],
                'id': data['id'],
                'content_length': len(content),
                'word_count': len(content.split()),
                's3_key': s3_key,
                'processed_at': datetime.now().isoformat()
            }
            result = self.db.text_metadata.insert_one(metadata)
            
            # Get the complete document and serialize it
            complete_doc = self.db.text_metadata.find_one({'_id': result.inserted_id})
            serialized_doc = self._serialize_mongodb_doc(complete_doc)
            
            # Index in Elasticsearch
            self.es_client.index(
                index='text_data',
                id=data['id'],
                document=serialized_doc
            )
            
        except Exception as e:
            self.logger.error(f"Error processing text: {str(e)}")
            success = False
        else:
            success = True
        
            # Record processing metrics
            self._record_metrics('text', start_time, success)

    def process_image(self, data: Dict[str, Any]) -> None:
        """Process image data and extract basic metadata"""
        start_time = datetime.now()
        
        try:
            # Decode base64 image
            image_data = base64.b64decode(data['content'])
            image = Image.open(io.BytesIO(image_data))
            
            # Store raw image in S3
            s3_key = f"images/{data['id']}.jpg"
            self.s3_client.put_object(
                Bucket=self.bucket_name,
                Key=s3_key,
                Body=image_data
            )
            
            # Store metadata in MongoDB
            metadata = {
                **data['metadata'],
                'id': data['id'],
                'dimensions': {
                    'width': image.width,
                    'height': image.height
                },
                'format': image.format,
                'mode': image.mode,
                's3_key': s3_key,
                'processed_at': datetime.now().isoformat()
            }
            result = self.db.image_metadata.insert_one(metadata)
            
            # Get the complete document and serialize it
            complete_doc = self.db.image_metadata.find_one({'_id': result.inserted_id})
            serialized_doc = self._serialize_mongodb_doc(complete_doc)
            
            # Index in Elasticsearch
            self.es_client.index(
                index='image_data',
                id=data['id'],
                document=serialized_doc
            )
            
        except Exception as e:
            self.logger.error(f"Error processing image: {str(e)}")
            success = False
        else:
            success = True
        
            # Record processing metrics
            self._record_metrics('image', start_time, success)

    def process_video(self, data: Dict[str, Any]) -> None:
        """Process video data and store metadata"""
        start_time = datetime.now()
        
        try:
            # Decode base64 video
            video_data = base64.b64decode(data['content'])
            
            # Store raw video in S3
            s3_key = f"videos/{data['id']}.mp4"
            self.s3_client.put_object(
                Bucket=self.bucket_name,
                Key=s3_key,
                Body=video_data
            )
            
            # Store metadata in MongoDB
            metadata = {
                **data['metadata'],
                'id': data['id'],
                'file_size': len(video_data),
                's3_key': s3_key,
                'processed_at': datetime.now().isoformat()
            }
            self.db.video_metadata.insert_one(metadata)
            
            # Index in Elasticsearch
            self.es_client.index(
                index='video_data',
                id=data['id'],
                document={
                    'content': content,
                    **metadata
                }
            )
            
        except Exception as e:
            self.logger.error(f"Error processing video: {str(e)}")
            success = False
        else:
            success = True
        
        # Record processing metrics
        self._record_metrics('video', start_time, success)

    def _record_metrics(self, data_type: str, start_time: datetime, success: bool) -> None:
        """Record processing metrics in Cassandra"""
        processing_time = (datetime.now() - start_time).total_seconds()
        try:
            self.cassandra_session.execute(
            """
                INSERT INTO data_processing.processing_metrics
                (id, timestamp, data_type, processing_time, success)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (uuid.uuid4(), datetime.now(), data_type, processing_time, success)
            )
            self.logger.info(f"Recorded metrics for {data_type}: {processing_time} seconds")
        except Exception as e:
            self.logger.error(f"Error recording metrics in Cassandra: {str(e)}")

    def process_message(self, message):
        """Process a single message with error handling"""
        try:
            if message is None:
                return

            data = json.loads(message.value())
            
            # Process based on data type
            if data['type'] == 'text':
                self.process_text(data)
            elif data['type'] == 'image':
                self.process_image(data)
            elif data['type'] == 'video':
                self.process_video(data)
            else:
                self.logger.warning(f"Unknown data type: {data['type']}")
                
            self.logger.info(f"Successfully processed message {data.get('id')}")
            
        except Exception as e:
            self.logger.error(f"Error processing message: {str(e)}")
            # Here you might want to implement dead letter queue logic
            
    def run(self):
        """Main processing loop"""
        try:
            self.consumer.subscribe(['text_data_topic', 'image_data_topic', 'video_data_topic'])
            
            while self.running:
                message = self.consumer.poll(1.0)
                
                if message is None:
                    continue
                    
                if message.error():
                    if message.error().code() == KafkaError._PARTITION_EOF:
                        continue
                    else:
                        self.logger.error(f"Kafka error: {message.error()}")
                        continue
                        
                self.process_message(message)
                
        except Exception as e:
            self.logger.error(f"Error in main processing loop: {str(e)}")
        finally:
            self.cleanup()

# Health check endpoint
@app.route('/health')
def health_check():
    return jsonify({"status": "healthy"}), 200

if __name__ == "__main__":
    processor = DataProcessor()
    
    # Start health check server
    from threading import Thread
    health_thread = Thread(target=lambda: app.run(host='0.0.0.0', port=8000))
    health_thread.daemon = True
    health_thread.start()
    
    # Start processing
    processor.run()
