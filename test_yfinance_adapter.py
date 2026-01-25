"""
Unit tests for yfinance adapter
"""

import unittest
import pandas as pd
from datetime import datetime
from yfinance_adapter import YFinanceAdapter


class TestYFinanceAdapter(unittest.TestCase):
    """Test yfinance data adapter"""
    
    def setUp(self):
        """Set up test fixtures"""
        self.adapter = YFinanceAdapter()
    
    def test_get_historical_data_basic(self):
        """Test basic data fetching"""
        print("\n=== Test: Basic Data Fetching ===")
        
        # Fetch AAPL data for 2024
        df = self.adapter.get_historical_data(
            'AAPL', 
            '1 day',
            start_date='2024-01-01',
            end_date='2024-12-31'
        )
        
        # Verify data was returned
        self.assertIsNotNone(df, "Should return data for AAPL")
        self.assertGreater(len(df), 200, "Should have at least 200 trading days")
        
        print(f"✓ Fetched {len(df)} bars for AAPL")
        print(f"✓ Date range: {df.index[0]} to {df.index[-1]}")
        print(f"✓ Columns: {list(df.columns)}")
        print(f"✓ Sample data:\n{df.head()}")
    
    def test_data_format_compatibility(self):
        """Test that data format matches IB format"""
        print("\n=== Test: Data Format Compatibility ===")
        
        df = self.adapter.get_historical_data(
            'MSFT',
            '1 day', 
            start_date='2024-01-01',
            end_date='2024-01-31'
        )
        
        # Check required columns exist
        required_columns = ['open', 'high', 'low', 'close', 'volume']
        for col in required_columns:
            self.assertIn(col, df.columns, f"Should have '{col}' column")
        
        # Check data types
        self.assertTrue(pd.api.types.is_datetime64_any_dtype(df.index), 
                       "Index should be datetime")
        self.assertTrue(pd.api.types.is_numeric_dtype(df['close']),
                       "Close should be numeric")
        
        # Check no timezone (to match IB)
        self.assertIsNone(df.index.tz, "Index should not have timezone")
        
        print(f"✓ All required columns present: {required_columns}")
        print(f"✓ Index type: {df.index.dtype}")
        print(f"✓ Close type: {df['close'].dtype}")
        print(f"✓ No timezone: {df.index.tz is None}")
    
    def test_timeframe_conversion(self):
        """Test timeframe conversion from IB to yfinance format"""
        print("\n=== Test: Timeframe Conversion ===")
        
        test_cases = [
            ('1 min', '1m'),
            ('5 mins', '5m'),
            ('15 mins', '15m'),
            ('1 hour', '1h'),
            ('1 day', '1d'),
            ('1W', '1wk'),
            ('1M', '1mo'),
        ]
        
        for ib_tf, expected_yf in test_cases:
            result = self.adapter._convert_timeframe(ib_tf)
            self.assertEqual(result, expected_yf, 
                           f"'{ib_tf}' should convert to '{expected_yf}'")
            print(f"✓ '{ib_tf}' → '{expected_yf}'")
    
    def test_multiple_symbols(self):
        """Test fetching data for multiple symbols"""
        print("\n=== Test: Multiple Symbols ===")
        
        symbols = ['AAPL', 'GOOGL', 'MSFT', 'NVDA']
        results = {}
        
        for symbol in symbols:
            df = self.adapter.get_historical_data(
                symbol,
                '1 day',
                start_date='2024-06-01',
                end_date='2024-06-30'
            )
            results[symbol] = df
            
            if df is not None:
                print(f"✓ {symbol}: {len(df)} bars, "
                      f"price range ${df['close'].min():.2f}-${df['close'].max():.2f}")
            else:
                print(f"✗ {symbol}: No data")
        
        # Verify all symbols returned data
        for symbol in symbols:
            self.assertIsNotNone(results[symbol], 
                               f"Should get data for {symbol}")
    
    def test_2025_data_availability(self):
        """Test that 2025 data is available (current year)"""
        print("\n=== Test: 2025 Data Availability ===")
        
        df = self.adapter.get_historical_data(
            'AAPL',
            '1 day',
            start_date='2025-01-01',
            end_date='2025-01-25'
        )
        
        self.assertIsNotNone(df, "Should have 2025 data")
        self.assertGreater(len(df), 0, "Should have at least some 2025 data")
        
        print(f"✓ 2025 data available: {len(df)} bars")
        print(f"✓ Date range: {df.index[0]} to {df.index[-1]}")
        print(f"✓ Latest close: ${df['close'].iloc[-1]:.2f}")
    
    def test_invalid_symbol(self):
        """Test handling of invalid symbols"""
        print("\n=== Test: Invalid Symbol Handling ===")
        
        df = self.adapter.get_historical_data(
            'INVALIDXYZ123',
            '1 day',
            start_date='2024-01-01',
            end_date='2024-01-31'
        )
        
        # Should return None for invalid symbols
        self.assertIsNone(df, "Should return None for invalid symbol")
        print("✓ Invalid symbol handled gracefully (returns None)")


def run_tests():
    """Run all tests with verbose output"""
    print("=" * 70)
    print(" YFINANCE ADAPTER UNIT TESTS")
    print("=" * 70)
    
    # Create test suite
    suite = unittest.TestLoader().loadTestsFromTestCase(TestYFinanceAdapter)
    
    # Run with verbose output
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    
    # Summary
    print("\n" + "=" * 70)
    print(" TEST SUMMARY")
    print("=" * 70)
    print(f"Tests run: {result.testsRun}")
    print(f"Successes: {result.testsRun - len(result.failures) - len(result.errors)}")
    print(f"Failures: {len(result.failures)}")
    print(f"Errors: {len(result.errors)}")
    
    if result.wasSuccessful():
        print("\n✅ ALL TESTS PASSED!")
    else:
        print("\n❌ SOME TESTS FAILED")
    
    return result.wasSuccessful()


if __name__ == '__main__':
    import sys
    success = run_tests()
    sys.exit(0 if success else 1)
