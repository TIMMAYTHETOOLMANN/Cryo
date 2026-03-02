#!/usr/bin/env python3
"""
Apache Kafka Client for Data Lake
Real-time streaming infrastructure for opportunity data
"""

import asyncio
import json
import time
from typing import Dict, List, Optional, Callable, Awaitable, Any
from dataclasses import dataclass
from enum import Enum

# Try to import kafka-python, fall back to mock if not available
try:
    from kafka import KafkaProducer, KafkaConsumer, KafkaAdminClient
    from kafka.admin import NewTopic
    from kafka.errors import KafkaError
    KAFKA_AVAILABLE = True
except ImportError:
    KAFKA_AVAILABLE = False
    KafkaError = Exception


class KafkaTopic(Enum):
    """Kafka topic names"""
    RAW_MEMPOOL = "raw-mempool"
    NEW_CONTRACTS = "new-contracts"
    SOCIAL_FEEDS = "social-feeds"
    BRIDGE_VOLUMES = "bridge-volumes"
    ORACLE_UPDATES = "oracle-updates"
    OPPORTUNITY_SIGNALS = "opportunity-signals"
    EXECUTION_COMMANDS = "execution-commands"
    EXECUTION_RESULTS = "execution-results"
    SYSTEM_METRICS = "system-metrics"


@dataclass
class KafkaConfig:
    """Kafka configuration"""
    bootstrap_servers: List[str]
    consumer_group: str = "omni-channel-consumer"
    auto_offset_reset: str = "latest"
    enable_auto_commit: bool = True
    security_protocol: str = "PLAINTEXT"
    ssl_cafile: Optional[str] = None
    ssl_certfile: Optional[str] = None
    ssl_keyfile: Optional[str] = None
    sasl_mechanism: str = "PLAIN"
    sasl_plain_username: Optional[str] = None
    sasl_plain_password: Optional[str] = None


class MockKafkaProducer:
    """Mock producer for when Kafka is not available"""
    
    def __init__(self, **kwargs):
        self.messages = []
        self.topics = set()
    
    def send(self, topic: str, value: bytes, key: bytes = None):
        self.messages.append({
            'topic': topic,
            'value': value,
            'key': key,
            'timestamp': time.time()
        })
        return FutureMock()
    
    def flush(self):
        pass
    
    def close(self):
        pass


class MockKafkaConsumer:
    """Mock consumer for when Kafka is not available"""
    
    def __init__(self, *topics, **kwargs):
        self.topics = topics
        self.messages = []
        self.subscribed = True
    
    def subscribe(self, topics: List[str]):
        self.topics = topics
        self.subscribed = True
    
    def poll(self, timeout_ms: int = 1000):
        return {}
    
    def __iter__(self):
        return self
    
    def __next__(self):
        time.sleep(0.1)
        raise StopIteration
    
    def close(self):
        pass


class FutureMock:
    """Mock future for producer"""
    
    def get(self, timeout: Optional[float] = None):
        return None


class KafkaDataLake:
    """
    Apache Kafka integration for the Omni-Channel Data Lake
    Handles publishing and subscribing to all data streams
    """
    
    def __init__(self, config: KafkaConfig):
        self.config = config
        self.producer = None
        self.consumers: Dict[str, Any] = {}
        self.is_running = False
        self._message_callbacks: Dict[str, List[Callable]] = {}
        
        if not KAFKA_AVAILABLE:
            print("⚠️  kafka-python not installed. Running in mock mode.")
            print("   Install with: pip install kafka-python")
    
    async def start(self):
        """Initialize Kafka connection and create topics"""
        print("\n📡 Initializing Kafka Data Lake...")
        
        if not KAFKA_AVAILABLE:
            # Use mock
            self.producer = MockKafkaProducer()
            print("   Running in MOCK mode (kafka-python not installed)")
        else:
            try:
                # Initialize producer
                self.producer = KafkaProducer(
                    bootstrap_servers=self.config.bootstrap_servers,
                    value_serializer=lambda v: json.dumps(v).encode('utf-8'),
                    key_serializer=lambda k: k.encode('utf-8') if k else None,
                    acks='all',
                    retries=3,
                    max_in_flight_requests_per_connection=5
                )
                
                # Create topics if they don't exist
                await self._create_topics()
                
                print("   ✅ Kafka producer initialized")
                print(f"   Bootstrap: {self.config.bootstrap_servers}")
                
            except Exception as e:
                print(f"   ⚠️  Kafka connection failed: {e}")
                print("   Falling back to mock mode")
                self.producer = MockKafkaProducer()
        
        self.is_running = True
    
    async def _create_topics(self):
        """Create required topics"""
        try:
            admin_client = KafkaAdminClient(
                bootstrap_servers=self.config.bootstrap_servers,
                client_id='omni-channel-admin'
            )
            
            topics = [
                NewTopic(name=topic.value, num_partitions=3, replication_factor=1)
                for topic in KafkaTopic
            ]
            
            admin_client.create_topics(new_topics=topics, validate_only=False)
            print(f"   ✅ Created {len(KafkaTopic)} topics")
            
        except Exception as e:
            print(f"   ⚠️  Topic creation failed (may already exist): {e}")
    
    async def publish(self, topic: KafkaTopic, message: Dict[str, Any], key: Optional[str] = None):
        """Publish message to topic"""
        if not self.is_running:
            return
        
        try:
            future = self.producer.send(
                topic.value,
                value=message,
                key=key
            )
            
            # Don't wait for confirmation in async mode
            # In production, you might want to handle failures
            
        except Exception as e:
            print(f"   ❌ Publish failed to {topic.value}: {e}")
    
    async def publish_mempool(self, transaction: Dict[str, Any]):
        """Publish mempool transaction"""
        await self.publish(KafkaTopic.RAW_MEMPOOL, transaction)
    
    async def publish_contract(self, contract_data: Dict[str, Any]):
        """Publish new contract deployment"""
        await self.publish(KafkaTopic.NEW_CONTRACTS, contract_data)
    
    async def publish_social(self, social_data: Dict[str, Any]):
        """Publish social media data"""
        await self.publish(KafkaTopic.SOCIAL_FEEDS, social_data)
    
    async def publish_bridge(self, bridge_data: Dict[str, Any]):
        """Publish bridge volume data"""
        await self.publish(KafkaTopic.BRIDGE_VOLUMES, bridge_data)
    
    async def publish_oracle(self, oracle_data: Dict[str, Any]):
        """Publish oracle update"""
        await self.publish(KafkaTopic.ORACLE_UPDATES, oracle_data)
    
    async def publish_signal(self, signal_data: Dict[str, Any]):
        """Publish opportunity signal"""
        await self.publish(KafkaTopic.OPPORTUNITY_SIGNALS, signal_data)
    
    async def subscribe(self, topic: KafkaTopic, callback: Callable[[Dict], Awaitable[None]]):
        """Subscribe to topic with callback"""
        if topic not in self._message_callbacks:
            self._message_callbacks[topic] = []
        
        self._message_callbacks[topic].append(callback)
        
        # Start consumer if not already running
        if topic not in self.consumers:
            await self._start_consumer(topic)
    
    async def _start_consumer(self, topic: KafkaTopic):
        """Start consumer for topic"""
        try:
            if not KAFKA_AVAILABLE:
                consumer = MockKafkaConsumer(topic.value)
            else:
                consumer = KafkaConsumer(
                    topic.value,
                    bootstrap_servers=self.config.bootstrap_servers,
                    group_id=self.config.consumer_group,
                    auto_offset_reset=self.config.auto_offset_reset,
                    enable_auto_commit=self.config.enable_auto_commit,
                    consumer_timeout_ms=1000,
                    value_deserializer=lambda m: json.loads(m.decode('utf-8')),
                    key_deserializer=lambda k: k.decode('utf-8') if k else None
                )
            
            self.consumers[topic.value] = consumer
            
            # Start consumer loop
            asyncio.create_task(self._consume_loop(topic))
            
        except Exception as e:
            print(f"   ❌ Consumer creation failed for {topic.value}: {e}")
    
    async def _consume_loop(self, topic: KafkaTopic):
        """Consume messages from topic"""
        consumer = self.consumers.get(topic.value)
        if not consumer:
            return
        
        callbacks = self._message_callbacks.get(topic, [])
        
        while self.is_running:
            try:
                records = consumer.poll(timeout_ms=1000)
                
                for topic_partition, messages in records.items():
                    for message in messages:
                        for callback in callbacks:
                            try:
                                await callback(message.value)
                            except Exception as e:
                                print(f"   ❌ Callback error: {e}")
                
                await asyncio.sleep(0.1)
                
            except Exception as e:
                print(f"   ❌ Consumer error: {e}")
                await asyncio.sleep(5)
    
    async def stop(self):
        """Shutdown Kafka connections"""
        print("\n📡 Shutting down Kafka Data Lake...")
        
        self.is_running = False
        
        if self.producer:
            try:
                self.producer.flush()
                self.producer.close()
            except:
                pass
        
        for consumer in self.consumers.values():
            try:
                consumer.close()
            except:
                pass
        
        print("   ✅ Kafka shutdown complete")
    
    def get_stats(self) -> Dict:
        """Get Data Lake statistics"""
        if isinstance(self.producer, MockKafkaProducer):
            return {
                'mode': 'mock',
                'messages_sent': len(self.producer.messages),
                'topics': list(self.producer.topics),
                'consumers': len(self.consumers)
            }
        else:
            return {
                'mode': 'production',
                'topics': [t.value for t in KafkaTopic],
                'consumers': len(self.consumers),
                'is_running': self.is_running
            }


# Convenience functions for direct usage
_async_data_lake: Optional[KafkaDataLake] = None


async def get_data_lake(config: Optional[KafkaConfig] = None) -> KafkaDataLake:
    """Get or create singleton Data Lake instance"""
    global _async_data_lake
    
    if not _async_data_lake:
        if not config:
            config = KafkaConfig(bootstrap_servers=['localhost:9092'])
        _async_data_lake = KafkaDataLake(config)
        await _async_data_lake.start()
    
    return _async_data_lake


async def publish_to_topic(topic: KafkaTopic, message: Dict[str, Any]):
    """Convenience function to publish message"""
    data_lake = await get_data_lake()
    await data_lake.publish(topic, message)
