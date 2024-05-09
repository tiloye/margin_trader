import pandas as pd
from datetime import datetime
from queue import Queue
from margin_trader.broker import Broker
from margin_trader.data_source import DataHandler
from margin_trader.event import FillEvent, OrderEvent
from margin_trader.performance import create_sharpe_ratio, create_drawdowns

class SimBroker(Broker):
    """
    Simulate live trading on a broker account.
    
    State:
        - Convert all order objects into their equivalent fill objects automatically
            without latency, slippage or fill-ratio issues.
        - Can only handle market order events
        - Execute all orders at close price
    """
    
    def __init__(
            self,
            balance: int|float,
            data_handler: DataHandler,
            events: Queue,
            leverage: int = 1,
            commission: None|int|float = None
        ):
        self.balance = balance
        self.equity = balance
        self.free_margin = balance
        self.margin = 0.0
        self.data_handler = data_handler
        self.events = events
        self.leverage = leverage
        self.commission = commission
        self.p_manager = PositionManager()

    def execute_order(self, event: OrderEvent) -> None:
        """
        Simply converts Order objects into Fill objects naively,
        i.e. without any latency, slippage or fill ratio problems.

        Parameters:
        event - Contains an Event object with order information.
        """
        if event.type == 'ORDER':
            fill_event = FillEvent(
                self.data_handler.current_datetime,
                event.symbol,
                event.units,
                event.side,
                self.data_handler.get_latest_close_price(event.symbol),
                self.commission
            )
            self.events.put(fill_event)
    
    def buy(self, symbol: str,
            order_type: str = "MKT", units: int|float = 100) -> None:
        """Buy x units of symbol."""
        self.__create_order(symbol, order_type, "BUY", units)

    def sell(self, symbol: str,
             order_type: str = "MKT", units: int|float = 100) -> None:
        """Sell x units of symbol."""
        self.__create_order(symbol, order_type, "SELL", units)

    def close(self, symbol: str, units: int|float = 100) -> None:
        """Close an existion position with an opposing order"""
        position = self.p_manager.positions.get(symbol, False)
        if position:
            side = position.side
            if side == "BUY":
                self.sell(symbol, units)
            else:
                self.buy(symbol, units)
        else:
            print(f"There is no open position for {symbol}")

    def __create_order(self, symbol: str, order_type: str,
                    side: str, units: int|float = 100) -> None:
        """Create an order event"""
        order = OrderEvent(symbol, order_type=order_type,
                           units=units, side=side)
        self.events.put(order)

    def update_account_from_fill(self, event: FillEvent) -> None:
        curr_margin = self.margin
        self.__update_position_from_fill(event)
        self.__update_margin_from_fill(event)
        self.__update_balance(curr_margin)

    def update_account_from_price(self) -> None:
        self.__update_positions_from_price()
        self.__update_equity()
        self.__update_free_margin()

    def __update_position_from_fill(self, event: FillEvent) -> None:
        """Add new positions to the porfolio"""
        self.p_manager.update_position_from_fill(event)

    def __update_positions_from_price(self) -> None:
        """Update portfolio holdings with the latest market price"""
        for symbol in self.p_manager.positions:
            self.p_manager.update_pnl(
                symbol,
                self.data_handler.get_latest_close_price(symbol)
            )
            
    def __update_balance(self, prev_margin: float) -> None:
        # Check if a position has been closed
        if self.margin < prev_margin:
            self.balance += self.p_manager.history[-1].pnl

    def __update_equity(self) -> None:
        total_pnl = self.p_manager.get_totat_pnl()
        self.equity += total_pnl

    def __update_free_margin(self) -> None:
        self.free_margin = self.equity - self.get_used_margin()

    def __update_margin_from_fill(self, event: FillEvent) -> None:
        if event.symbol in self.p_manager.positions: # Closing a position
            self.margin -= (self.p_manager.positions[event.symbol].get_cost()
                            / self.leverage)
        else: # Opening a position
            self.margin += (event.units * event.fill_price) / self.leverage
    
    def get_used_margin(self) -> float:
        symbols = self.p_manager.positions.keys()
        margin = sum(self.p_manager.positions[symbol].get_cost() for symbol in symbols)
        margin = margin/self.leverage
        return margin
    
    def get_positions(self):
        pass


class PositionManager:
    """Open and close positions based on filled orders."""
    
    def __init__(self):
        self.positions = {}
        self.history = []

    def update_pnl(self, symbol: str, price: float) -> None:
        """Update position PnL when from market event"""
        self.positions[symbol].update(price)

    def update_position_from_fill(self, event: FillEvent) -> None:
        """Add/remove a position for recently filled order."""

        if event.symbol not in self.positions: # Position does not exist. Open a trade
            self.__open_position(event)
        else: # Position already exists. Close the trade
            self.__close_position(event)
            
    def __open_position(self, event: FillEvent) -> None:
        self.positions[event.symbol] = Position(
                timeindex=event.timeindex,
                symbol=event.symbol,
                units=event.units,
                fill_price=event.fill_price,
                commission=event.commission,
                side=event.side
        )
        
    def __close_position(self, event: FillEvent) -> None:
        self.positions[event.symbol].commission += event.commission # openNclose fee
        self.positions[event.symbol].update(event.fill_price)
        self.positions[event.symbol].update_close_time(event.timeindex)
        self.history.append(self.positions[event.symbol])
        del self.positions[event.symbol]

    def get_total_pnl(self) -> int:
        total_pnl = sum(self.positions[symbol].pnl for symbol in self.positions)
        return total_pnl
    

class Position:
    def __init__(
            self,
            timeindex: str|datetime,
            symbol: str,
            units: int|float,
            fill_price: float,
            commission: float|None,
            side: str
        ):
        self.symbol = symbol
        self.units = units
        self.fill_price = fill_price
        self.last_price = fill_price
        self.commission = commission
        self.side = side
        self.open_time = timeindex
        self.pnl = 0
        self.__cost = self.fill_price * self.units

    def update_pnl(self) -> None:
        pnl = (self.last_price - self.fill_price) * self.units
        if self.side == "BUY":
            self.pnl = pnl - self.commission
        else:
            self.pnl = -1 * pnl - self.commission

    def update_last_price(self, price: float) -> None:
        self.last_price = price

    def update(self, price: float) -> None:
        self.update_last_price(price)
        self.update_pnl()
    
    def update_close_time(self, timeindex: str|datetime) -> None:
        self.close_time = timeindex

    def get_cost(self) -> float:
        return self.__cost

    def __repr__(self) -> str:
        position = f"{self.symbol}|{self.side}|{self.units}|{self.pnl}"
        return position
    