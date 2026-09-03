#!/usr/bin/env python3
"""
Log Forwarder Test Script

Tests the complete log forwarding workflow:
1. LogForwarder initialization and configuration
2. Log collection and buffering
3. Log forwarding to backend server
4. Backend API endpoint validation
5. Request signing verification
"""

import asyncio
import json
import logging
import sys
import time
import uuid
import hashlib
import hmac
from datetime import datetime
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

import aiohttp

# Configure test logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s',
)
logger = logging.getLogger('test_log_forwarder')


class LogForwarderTestConfig:
    """Test configuration"""
    BACKEND_URL = "http://localhost:8000"
    HOST_ID = "568d7f6e-2bf3-4556-bb0c-26105e35c5ec"
    REQUEST_SIGNING_SECRET = "dev-secret-key-for-testing"
    SSL_VERIFY = False
    BATCH_SIZE = 10
    MAX_BUFFER_SIZE = 100
    SEND_INTERVAL = 5


def generate_signature(host_id: str, secret: str, payload: dict) -> dict:
    """Generate request signature"""
    timestamp_int = int(time.time())
    nonce = str(uuid.uuid4())
    
    payload['signature'] = ''
    payload['nonce'] = nonce
    payload['timestamp_int'] = timestamp_int
    
    body_str = json.dumps(payload, sort_keys=True)
    message = f"{host_id}:{timestamp_int}:{nonce}:{body_str}"
    signature = hmac.new(
        secret.encode('utf-8'),
        message.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()
    
    payload['signature'] = signature
    return payload


async def test_backend_api_availability():
    """Test 1: Verify backend API is available"""
    logger.info("=" * 60)
    logger.info("Test 1: Backend API Availability Check")
    logger.info("=" * 60)
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{LogForwarderTestConfig.BACKEND_URL}/api/taurus/host-log/") as resp:
                if resp.status in [200, 401, 403]:
                    logger.info(f"✓ Backend API is available (HTTP {resp.status})")
                    return True
                else:
                    logger.error(f"✗ Backend API returned unexpected status: {resp.status}")
                    return False
    except Exception as e:
        logger.error(f"✗ Backend API is not reachable: {e}")
        return False


async def test_log_receive_endpoint():
    """Test 2: Test log receive endpoint with sample logs"""
    logger.info("=" * 60)
    logger.info("Test 2: Log Receive Endpoint Test")
    logger.info("=" * 60)
    
    test_logs = [
        {
            'program_name': 'taurus-executor',
            'log_level': 'INFO',
            'message': 'Test log message 1: Executor started successfully',
            'source_file': '/opt/taurus/executor/main.py',
            'source_line': 42,
            'process_id': 1234,
            'thread_id': 5678,
            'log_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'extra_info': {'test': True},
        },
        {
            'program_name': 'taurus-supervisor',
            'log_level': 'WARNING',
            'message': 'Test log message 2: High memory usage detected',
            'source_file': '/opt/taurus/supervisor/monitor.py',
            'source_line': 128,
            'process_id': 1000,
            'thread_id': 2000,
            'log_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'extra_info': {'memory_usage': 85.5},
        },
        {
            'program_name': None,
            'log_level': 'ERROR',
            'message': 'Test log message 3: Connection timeout to backend',
            'source_file': '/opt/taurus/supervisor/heartbeat.py',
            'source_line': 256,
            'process_id': 1000,
            'thread_id': 3000,
            'log_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'extra_info': {'timeout': 30},
        },
    ]
    
    payload = {
        'host_id': LogForwarderTestConfig.HOST_ID,
        'logs': test_logs,
    }
    
    # Add signature
    if LogForwarderTestConfig.REQUEST_SIGNING_SECRET:
        payload = generate_signature(
            LogForwarderTestConfig.HOST_ID,
            LogForwarderTestConfig.REQUEST_SIGNING_SECRET,
            payload,
        )
    
    try:
        async with aiohttp.ClientSession() as session:
            url = f"{LogForwarderTestConfig.BACKEND_URL}/api/taurus/host-log/receive/"
            async with session.post(url, json=payload) as resp:
                response_data = await resp.json()
                
                if resp.status == 200 and response_data.get('code') == 2000:
                    received_count = response_data.get('data', {}).get('received_count', 0)
                    logger.info(f"✓ Log receive endpoint test passed")
                    logger.info(f"  - Sent {len(test_logs)} logs")
                    logger.info(f"  - Received {received_count} logs")
                    return True
                else:
                    logger.error(f"✗ Log receive endpoint test failed")
                    logger.error(f"  - HTTP Status: {resp.status}")
                    logger.error(f"  - Response: {response_data}")
                    return False
    except Exception as e:
        logger.error(f"✗ Log receive endpoint test failed: {e}")
        return False


async def test_log_forwarder_module():
    """Test 3: Test LogForwarder module directly"""
    logger.info("=" * 60)
    logger.info("Test 3: LogForwarder Module Test")
    logger.info("=" * 60)
    
    try:
        from taurus_supervisor.log_forwarder import LogForwarder
        
        forwarder = LogForwarder(
            server_url=LogForwarderTestConfig.BACKEND_URL,
            host_id=LogForwarderTestConfig.HOST_ID,
            request_signing_secret=LogForwarderTestConfig.REQUEST_SIGNING_SECRET,
            ssl_verify=LogForwarderTestConfig.SSL_VERIFY,
            batch_size=LogForwarderTestConfig.BATCH_SIZE,
            max_buffer_size=LogForwarderTestConfig.MAX_BUFFER_SIZE,
            send_interval=LogForwarderTestConfig.SEND_INTERVAL,
        )
        
        logger.info("✓ LogForwarder initialized successfully")
        
        # Enable collection mode
        forwarder.set_collect_mode(min_level='DEBUG', programs=None, duration=60)
        logger.info("✓ Collection mode enabled")
        
        # Collect test logs
        test_messages = [
            ('taurus-executor', 'INFO', 'Executor heartbeat sent'),
            ('taurus-executor', 'WARNING', 'High CPU usage detected'),
            ('taurus-supervisor', 'ERROR', 'Failed to connect to program'),
            (None, 'DEBUG', 'Supervisor internal debug message'),
        ]
        
        for program_name, level, message in test_messages:
            forwarder.collect_log(
                program_name=program_name,
                log_level=level,
                message=message,
            )
        
        buffer_size = forwarder.get_log_buffer_size()
        logger.info(f"✓ Collected {buffer_size} logs into buffer")
        
        # Check statistics
        stats = forwarder.get_statistics()
        logger.info(f"  - Total collected: {stats['total_collected']}")
        logger.info(f"  - Total sent: {stats['total_sent']}")
        logger.info(f"  - Total failed: {stats['total_failed']}")
        logger.info(f"  - Buffer size: {stats['buffer_size']}")
        
        # Start forwarder and send logs
        await forwarder.start()
        logger.info("✓ LogForwarder started")
        
        # Wait for send interval
        await asyncio.sleep(LogForwarderTestConfig.SEND_INTERVAL + 2)
        
        # Check statistics after send
        stats_after = forwarder.get_statistics()
        logger.info(f"  - Total sent (after): {stats_after['total_sent']}")
        logger.info(f"  - Total failed (after): {stats_after['total_failed']}")
        logger.info(f"  - Buffer size (after): {stats_after['buffer_size']}")
        
        if stats_after['total_sent'] > 0:
            logger.info("✓ Logs forwarded successfully")
        else:
            logger.warning("⚠ No logs were sent (check backend connectivity)")
        
        # Stop forwarder
        await forwarder.stop()
        logger.info("✓ LogForwarder stopped")
        
        return True
        
    except Exception as e:
        logger.error(f"✗ LogForwarder module test failed: {e}", exc_info=True)
        return False


async def test_signature_verification():
    """Test 4: Test request signing mechanism"""
    logger.info("=" * 60)
    logger.info("Test 4: Request Signing Test")
    logger.info("=" * 60)
    
    payload = {
        'host_id': LogForwarderTestConfig.HOST_ID,
        'logs': [{'message': 'test', 'log_level': 'INFO'}],
    }
    
    # Generate signature
    signed_payload = generate_signature(
        LogForwarderTestConfig.HOST_ID,
        LogForwarderTestConfig.REQUEST_SIGNING_SECRET,
        payload.copy(),
    )
    
    logger.info("✓ Signature generated successfully")
    logger.info(f"  - Timestamp: {signed_payload['timestamp_int']}")
    logger.info(f"  - Nonce: {signed_payload['nonce']}")
    logger.info(f"  - Signature: {signed_payload['signature'][:16]}...")
    
    # Verify signature format
    required_fields = ['signature', 'nonce', 'timestamp_int']
    missing_fields = [f for f in required_fields if f not in signed_payload]
    
    if missing_fields:
        logger.error(f"✗ Missing signature fields: {missing_fields}")
        return False
    
    logger.info("✓ All signature fields present")
    
    # Test with backend
    try:
        async with aiohttp.ClientSession() as session:
            url = f"{LogForwarderTestConfig.BACKEND_URL}/api/taurus/host-log/receive/"
            async with session.post(url, json=signed_payload) as resp:
                response_data = await resp.json()
                
                if resp.status == 200 and response_data.get('code') == 2000:
                    logger.info("✓ Signature verification passed by backend")
                    return True
                else:
                    logger.warning(f"⚠ Backend response: {response_data}")
                    return False
    except Exception as e:
        logger.error(f"✗ Signature test failed: {e}")
        return False


async def test_log_level_filtering():
    """Test 5: Test log level filtering"""
    logger.info("=" * 60)
    logger.info("Test 5: Log Level Filtering Test")
    logger.info("=" * 60)
    
    try:
        from taurus_supervisor.log_forwarder import LogForwarder
        
        forwarder = LogForwarder(
            server_url=LogForwarderTestConfig.BACKEND_URL,
            host_id=LogForwarderTestConfig.HOST_ID,
            min_log_level='WARNING',
        )
        
        # Enable collection with WARNING level
        forwarder.set_collect_mode(min_level='WARNING', programs=None, duration=60)
        
        # Collect logs at different levels
        forwarder.collect_log(program_name='test', log_level='DEBUG', message='Debug message')
        forwarder.collect_log(program_name='test', log_level='INFO', message='Info message')
        forwarder.collect_log(program_name='test', log_level='WARNING', message='Warning message')
        forwarder.collect_log(program_name='test', log_level='ERROR', message='Error message')
        forwarder.collect_log(program_name='test', log_level='CRITICAL', message='Critical message')
        
        buffer_size = forwarder.get_log_buffer_size()
        
        # Should only have WARNING, ERROR, CRITICAL (3 logs)
        if buffer_size == 3:
            logger.info(f"✓ Log level filtering works correctly (filtered to {buffer_size} logs)")
            return True
        else:
            logger.error(f"✗ Log level filtering failed (expected 3, got {buffer_size})")
            return False
        
    except Exception as e:
        logger.error(f"✗ Log level filtering test failed: {e}", exc_info=True)
        return False


async def test_program_filtering():
    """Test 6: Test program-specific log filtering"""
    logger.info("=" * 60)
    logger.info("Test 6: Program Filtering Test")
    logger.info("=" * 60)
    
    try:
        from taurus_supervisor.log_forwarder import LogForwarder
        
        forwarder = LogForwarder(
            server_url=LogForwarderTestConfig.BACKEND_URL,
            host_id=LogForwarderTestConfig.HOST_ID,
        )
        
        # Enable collection for specific programs only
        forwarder.set_collect_mode(
            min_level='INFO',
            programs=['taurus-executor', 'taurus-monitor'],
            duration=60,
        )
        
        # Collect logs from different programs
        forwarder.collect_log(program_name='taurus-executor', log_level='INFO', message='Executor log')
        forwarder.collect_log(program_name='taurus-monitor', log_level='INFO', message='Monitor log')
        forwarder.collect_log(program_name='taurus-supervisor', log_level='INFO', message='Supervisor log')
        forwarder.collect_log(program_name=None, log_level='INFO', message='Unknown program log')
        
        buffer_size = forwarder.get_log_buffer_size()
        
        # Should only have taurus-executor and taurus-monitor (2 logs)
        if buffer_size == 2:
            logger.info(f"✓ Program filtering works correctly (filtered to {buffer_size} logs)")
            return True
        else:
            logger.error(f"✗ Program filtering failed (expected 2, got {buffer_size})")
            return False
        
    except Exception as e:
        logger.error(f"✗ Program filtering test failed: {e}", exc_info=True)
        return False


async def run_all_tests():
    """Run all tests"""
    logger.info("=" * 60)
    logger.info("Log Forwarder Test Suite")
    logger.info("=" * 60)
    logger.info(f"Backend URL: {LogForwarderTestConfig.BACKEND_URL}")
    logger.info(f"Host ID: {LogForwarderTestConfig.HOST_ID}")
    logger.info(f"SSL Verify: {LogForwarderTestConfig.SSL_VERIFY}")
    logger.info("")
    
    results = {}
    
    # Run tests
    results['Backend API Availability'] = await test_backend_api_availability()
    logger.info("")
    
    results['Log Receive Endpoint'] = await test_log_receive_endpoint()
    logger.info("")
    
    results['LogForwarder Module'] = await test_log_forwarder_module()
    logger.info("")
    
    results['Signature Verification'] = await test_signature_verification()
    logger.info("")
    
    results['Log Level Filtering'] = await test_log_level_filtering()
    logger.info("")
    
    results['Program Filtering'] = await test_program_filtering()
    logger.info("")
    
    # Summary
    logger.info("=" * 60)
    logger.info("Test Summary")
    logger.info("=" * 60)
    
    passed = sum(1 for v in results.values() if v)
    total = len(results)
    
    for test_name, result in results.items():
        status = "✓ PASSED" if result else "✗ FAILED"
        logger.info(f"{test_name}: {status}")
    
    logger.info("")
    logger.info(f"Total: {passed}/{total} tests passed")
    
    if passed == total:
        logger.info("🎉 All tests passed!")
        return 0
    else:
        logger.warning(f"⚠ {total - passed} test(s) failed")
        return 1


if __name__ == '__main__':
    exit_code = asyncio.run(run_all_tests())
    sys.exit(exit_code)