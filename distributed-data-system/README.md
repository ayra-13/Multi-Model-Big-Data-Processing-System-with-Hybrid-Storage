# Distributed Data Processing System

A scalable, fault-tolerant distributed system for processing various types of data (text, images, videos) using modern cloud-native technologies.

## System Architecture

### Components
- **Data Ingestion**: Apache Kafka
- **Data Storage**: 
  - S3: Raw data storage
  - MongoDB: Metadata and user data
  - Cassandra: Time-series data
  - HDFS: Distributed storage for batch processing
- **Processing**:
  - Batch: Apache Spark
  - Stream: Kafka Streams
- **Search & Query**: Elasticsearch
- **Monitoring**: Grafana
- **Orchestration**: Docker Compose

## Quick Start with Docker

### Prerequisites
- Docker
- Docker Compose
- At least 8GB RAM available for containers
- 20GB free disk space

### Starting the System

1. Clone the repository:
   ```bash
   git clone <repository-url>
   cd distributed-data-system
   ```

2. Start all services:
   ```bash
   docker-compose up -d
   ```

   The system will automatically:
   - Start all required infrastructure services
   - Create necessary Kafka topics
   - Initialize databases and storage
   - Start data generator and processor services

3. Monitor the startup progress:
   ```bash
   docker-compose logs -f
   ```

### Service Health Checks

All services have built-in health checks. Check their status with:
```bash
docker-compose ps
```

### Service URLs and Ports

- **Data Services**:
  - Kafka: localhost:9092
  - MongoDB: localhost:27017
  - Elasticsearch: http://localhost:9200
  - Cassandra: localhost:9042
  - MinIO (S3): 
    - API: http://localhost:9000
    - Console: http://localhost:9001

- **Monitoring**:
  - Grafana: http://localhost:3000
  - Prometheus: http://localhost:9090

### Default Credentials

#### MinIO
- Username: minioadmin
- Password: minioadmin

#### MongoDB
- Username: root
- Password: example

#### Grafana
- Username: admin
- Password: admin

### Monitoring and Management

1. **Grafana Dashboards**:
   - Access Grafana at http://localhost:3000
   - Log in with the default credentials
   - Navigate to Dashboards > Browse
   - Available dashboards:
     - Data Processing Overview
     - Kafka Metrics
     - System Resources

2. **Data Exploration**:
   - Use MinIO Console to browse raw data
   - Use MongoDB Compass to explore metadata
   - Use Elasticsearch Kibana for search capabilities

### Scaling the System

The system can be scaled by adjusting the following:

1. **Data Processor Resources**:
   ```yaml
   data-processor:
     deploy:
       resources:
         limits:
           cpus: '2'
           memory: 4G
   ```

2. **Multiple Processor Instances**:
   ```bash
   docker-compose up -d --scale data-processor=3
   ```

### Maintenance

#### Viewing Logs
```bash
# All services
docker-compose logs -f

# Specific service
docker-compose logs -f data-processor
```

#### Restarting Services
```bash
# Restart all
docker-compose restart

# Restart specific service
docker-compose restart data-processor
```

#### Cleaning Up
```bash
# Stop all services
docker-compose down

# Remove all data volumes
docker-compose down -v
```

### Troubleshooting

1. **Service Won't Start**:
   - Check logs: `docker-compose logs [service-name]`
   - Verify resource availability
   - Ensure all dependent services are healthy

2. **Performance Issues**:
   - Monitor resource usage in Grafana
   - Check service logs for bottlenecks
   - Adjust resource limits in docker-compose.yml

3. **Data Processing Delays**:
   - Check Kafka consumer lag
   - Monitor processing metrics in Grafana
   - Verify network connectivity between services
