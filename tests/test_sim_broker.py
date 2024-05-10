import os
import csv
import unittest
from pathlib import Path
from queue import Queue
from margin_trader.data_source import HistoricCSVDataHandler
from margin_trader.broker.sim_broker import SimBroker

CSV_DIR = Path(__file__).parent
symbols = ["AAPL"]

class TestSimBroker(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        data = [
            ["2024-05-03", 100.0, 105.0, 98.0, 102.0, 102.0, 0],
            ["2024-05-04", 102.0, 108.0, 100.0, 106.0, 106.0, 0],
            ["2024-05-07", 106.0, 110.0, 104.0, 108.0, 108.0, 0],
            ["2024-05-08", 108.0, 112.0, 106.0, 110.0, 110.0, 0],
            ["2024-05-09", 110.0, 115.0, 108.0, 112.0, 112.0, 0]
        ]

        with open(CSV_DIR/"AAPL.csv", "w") as csvfile:
            writer = csv.writer(csvfile)
            writer.writerow(
                ["Date", "Open", "High", "Low", "Close", "Adj Close", "Volume"]
            )
            for day in data:
                writer.writerow(day)

    def setUp(self):
        self.event_queue = Queue()
        self.data_handler = HistoricCSVDataHandler(
            events = self.event_queue,
            csv_dir = CSV_DIR,
            symbol_list = symbols
        )
        self.broker = SimBroker(
            balance = 100_000.0,
            data_handler = self.data_handler,
            events = self.event_queue,
            commission = 0.5
        )
        self.data_handler.update_bars() # Add market event to the event queue

    def test_buy(self):
        _ = self.event_queue.get(False) # Generate signal from market event
        symbol = "AAPL"
        self.broker.buy(symbol)
        event = self.event_queue.get(False)

        self.assertEqual(event.type, "ORDER")
        self.assertEqual(event.symbol, symbol)
        self.assertEqual(event.side, "BUY")
        self.assertEqual(event.units, 100)
        self.assertEqual(event.order_type, "MKT")

    def test_sell(self):
        _ = self.event_queue.get(False)
        symbol = "AAPL"
        self.broker.sell(symbol)
        event = self.event_queue.get(False)

        self.assertEqual(event.type, "ORDER")
        self.assertEqual(event.symbol, symbol)
        self.assertEqual(event.side, "SELL")
        self.assertEqual(event.units, 100)
        self.assertEqual(event.order_type, "MKT")

    def test_execute_order(self):
        _ = self.event_queue.get(False)

        self.broker.buy("AAPL")
        order_event = self.event_queue.get(False)
        self.broker.execute_order(order_event)
        fill_event = self.event_queue.get(False)

        self.assertEqual(fill_event.type, "FILL")
        self.assertEqual(fill_event.side, "BUY")
        self.assertEqual(fill_event.timeindex.strftime("%Y-%m-%d"), "2024-05-03")
        self.assertEqual(fill_event.fill_price, 102.0)

    def test_update_account_from_fill_open(self):
        _ = self.event_queue.get(False)

        self.broker.buy("AAPL")
        order_event = self.event_queue.get(False)
        self.broker.execute_order(order_event)
        fill_event = self.event_queue.get(False)
        self.broker.update_account_from_fill(fill_event)

        self.assertIn(fill_event.symbol, self.broker.get_positions())
        self.assertEqual(self.broker.get_position(fill_event.symbol).units, 100)
        self.assertEqual(self.broker.margin, 10_200.0)
        self.assertEqual(self.broker.balance, 100_000.0)

    def test_update_account_from_fill_close(self):
        _ = self.event_queue.get(False)
        
        # Open a position
        self.broker.buy("AAPL")
        order_event = self.event_queue.get(False)
        self.broker.execute_order(order_event)
        fill_event = self.event_queue.get(False)
        self.broker.update_account_from_fill(fill_event)

        # Close position after price increased
        self.data_handler.update_bars()
        _ = self.event_queue.get(False)
        self.broker.close("AAPL")
        order_event = self.event_queue.get(False)
        self.broker.execute_order(order_event)
        fill_event = self.event_queue.get(False)
        self.broker.update_account_from_fill(fill_event)

        self.assertNotIn(fill_event.symbol, self.broker.get_positions())
        self.assertEqual(self.broker.margin, 0.0)
        self.assertEqual(self.broker.balance, 100_399.0)
        self.assertEqual(self.broker.equity, 100_399.0)

    def test_update_account_from_price(self):
        _ = self.event_queue.get(False)
        
        # Open a position
        self.broker.buy("AAPL")
        order_event = self.event_queue.get(False)
        self.broker.execute_order(order_event)
        fill_event = self.event_queue.get(False)
        self.broker.update_account_from_fill(fill_event)

        # Update account from market data
        self.data_handler.update_bars()
        mkt_event = self.event_queue.get(False)
        self.broker.update_account_from_price(mkt_event)
        account_info = self.broker.account_history[-1]
        
        self.assertEqual(self.broker.equity, 100_399.5)
        self.assertEqual(self.broker.free_margin, 90_199.5)
        self.assertEqual(account_info["timeindex"], self.data_handler.current_datetime)
        self.assertEqual(account_info["balance"], 100_000.0)
        self.assertEqual(account_info["equity"], 100_399.5)
    
    def test_check_pending_orders(self):
        self.broker._exec_bar = "next" # Change broker MKT execution price to next open
        _ = self.event_queue.get(False)

        self.broker.buy("AAPL")
        self.broker.check_pending_orders()
        order_event = self.event_queue.get(False)
        self.assertEqual(order_event.status, "PENDING")
        self.assertEqual(order_event.order_type, "MKT")

    def test_order_execution_at_open_price(self):
        self.broker._exec_bar = "next"
        _ = self.event_queue.get(False)

        self.broker.buy("AAPL")
        self.broker.check_pending_orders()
        order_event = self.event_queue.get(False)
        self.broker.execute_order(order_event)
        fill_event = self.event_queue.get(False)
        self.broker.update_account_from_fill(fill_event)
        
        self.assertEqual(fill_event.fill_price, 100.0)
        self.assertEqual(self.broker.margin, 10_000.0)
    
    @classmethod
    def tearDownClass(cls):
        if os.path.exists(CSV_DIR/"AAPL.csv"):
            os.remove(CSV_DIR/"AAPL.csv")


if __name__ == "__main__":
    unittest.main()