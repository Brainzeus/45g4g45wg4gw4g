import asyncio
import hashlib
import json
import time
import random
import string
from decimal import Decimal
from typing import List, Dict, Any, Set, Optional, Union
from collections import defaultdict
import logging
import base58
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat, PrivateFormat, NoEncryption, load_pem_private_key
from cryptography.hazmat.backends import default_backend
import websockets
import ssl
import os
import sys
from fractions import Fraction
import aiosqlite
import uvloop
from aiohttp import web

# Configurare logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Constante globale
MAX_TRANSACTIONS_PER_BLOCK = 10000
TARGET_BLOCK_TIME = Fraction(3, 10000000)  # 0.0000003 secunde
INITIAL_BRAINERS_SUPPLY = Fraction(5000000000, 1)
MIN_FEE = Fraction(1, 1000)  # 0.001 BRAINERS
MAX_FEE = Fraction(1, 100)   # 0.01 BRAINERS
GIFT_VALIDATOR_BURN = Fraction(6000, 1)  # 6000 BRAINERS
MIN_LIQUIDITY_DEX = Fraction(1000000, 1)  # 1 milion BRAINERS
MIN_LIQUIDITY_TTF = Fraction(500000, 1)  # 500k BRAINERS

class BrainersJSONEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Fraction):
            return str(obj)
        if isinstance(obj, set):
            return list(obj)
        return super().default(obj)

class Transaction:
    def __init__(self, sender: str, recipient: str, amount: Fraction, transaction_type: str, fee: Fraction, data: Dict[str, Any] = None, signature: str = None):
        self.sender = sender
        self.recipient = recipient
        self.amount = amount
        self.transaction_type = transaction_type
        self.fee = fee
        self.data = data or {}
        self.signature = signature
        self.timestamp = time.time()
        self.hash = self.calculate_hash()

    def calculate_hash(self):
        transaction_data = f"{self.sender}{self.recipient}{self.amount}{self.transaction_type}{self.fee}{json.dumps(self.data, sort_keys=True)}{self.timestamp}"
        return hashlib.sha256(transaction_data.encode()).hexdigest()

    def sign(self, private_key: ec.EllipticCurvePrivateKey):
        transaction_data = self.calculate_hash().encode()
        self.signature = base58.b58encode(private_key.sign(
            transaction_data,
            ec.ECDSA(hashes.SHA256())
        )).decode()

    def verify_signature(self, public_key: ec.EllipticCurvePublicKey) -> bool:
        try:
            signature = base58.b58decode(self.signature)
            transaction_data = self.calculate_hash().encode()
            public_key.verify(
                signature,
                transaction_data,
                ec.ECDSA(hashes.SHA256())
            )
            return True
        except:
            return False

    def to_dict(self):
        return {
            "sender": self.sender,
            "recipient": self.recipient,
            "amount": str(self.amount),
            "transaction_type": self.transaction_type,
            "fee": str(self.fee),
            "data": self.data,
            "signature": self.signature,
            "timestamp": self.timestamp,
            "hash": self.hash
        }

    @classmethod
    def from_dict(cls, data):
        tx = cls(
            data['sender'],
            data['recipient'],
            Fraction(data['amount']),
            data['transaction_type'],
            Fraction(data['fee']),
            data.get('data'),
            data['signature']
        )
        tx.timestamp = data['timestamp']
        tx.hash = data['hash']
        return tx

class Block:
    def __init__(self, index: int, transactions: List[Transaction], timestamp: float, previous_hash: str, validator: str):
        self.index = index
        self.transactions = transactions
        self.timestamp = timestamp
        self.previous_hash = previous_hash
        self.validator = validator
        self.merkle_root = self.calculate_merkle_root()
        self.hash = self.calculate_hash()

    def calculate_merkle_root(self):
        if not self.transactions:
            return hashlib.sha256(b"").hexdigest()
        transaction_hashes = [tx.hash for tx in self.transactions]
        while len(transaction_hashes) > 1:
            new_hashes = []
            for i in range(0, len(transaction_hashes), 2):
                if i + 1 < len(transaction_hashes):
                    combined_hash = hashlib.sha256((transaction_hashes[i] + transaction_hashes[i+1]).encode()).hexdigest()
                else:
                    combined_hash = hashlib.sha256((transaction_hashes[i] + transaction_hashes[i]).encode()).hexdigest()
                new_hashes.append(combined_hash)
            transaction_hashes = new_hashes
        return transaction_hashes[0]

    def calculate_hash(self):
        block_data = {
            "index": self.index,
            "merkle_root": self.merkle_root,
            "timestamp": self.timestamp,
            "previous_hash": self.previous_hash,
            "validator": self.validator
        }
        return hashlib.sha256(json.dumps(block_data, sort_keys=True).encode()).hexdigest()

    def to_dict(self):
        return {
            "index": self.index,
            "transactions": [tx.to_dict() for tx in self.transactions],
            "timestamp": self.timestamp,
            "previous_hash": self.previous_hash,
            "validator": self.validator,
            "merkle_root": self.merkle_root,
            "hash": self.hash
        }

    @classmethod
    def from_dict(cls, data):
        transactions = [Transaction.from_dict(tx) for tx in data['transactions']]
        block = cls(data['index'], transactions, data['timestamp'], data['previous_hash'], data['validator'])
        block.merkle_root = data['merkle_root']
        block.hash = data['hash']
        return block

class Token:
    def __init__(self, name: str, symbol: str, total_supply: Fraction, creator: str, is_minable: bool = False, difficulty: int = 0):
        self.name = name
        self.symbol = symbol
        self.total_supply = total_supply
        self.circulating_supply = Fraction(0)
        self.creator = creator
        self.is_minable = is_minable
        self.difficulty = difficulty
        self.address = self.generate_address()
        self.holders = defaultdict(Fraction)

    def generate_address(self):
        token_data = f"{self.name}{self.symbol}{self.total_supply}{self.creator}{time.time()}"
        token_hash = hashlib.sha256(token_data.encode()).hexdigest()
        return f"0xBrainers{token_hash[:34]}"

    def mint(self, amount: Fraction, recipient: str):
        if self.circulating_supply + amount > self.total_supply:
            raise ValueError("Minting would exceed total supply")
        self.circulating_supply += amount
        self.holders[recipient] += amount

    def burn(self, amount: Fraction, holder: str):
        if self.holders[holder] < amount:
            raise ValueError("Insufficient balance to burn")
        self.holders[holder] -= amount
        self.circulating_supply -= amount

    def transfer(self, sender: str, recipient: str, amount: Fraction):
        if self.holders[sender] < amount:
            raise ValueError("Insufficient balance to transfer")
        self.holders[sender] -= amount
        self.holders[recipient] += amount

    def to_dict(self):
        return {
            "name": self.name,
            "symbol": self.symbol,
            "total_supply": str(self.total_supply),
            "circulating_supply": str(self.circulating_supply),
            "creator": self.creator,
            "is_minable": self.is_minable,
            "difficulty": self.difficulty,
            "address": self.address
        }

class Wallet:
    def __init__(self, private_key=None):
        if private_key:
            self.private_key = load_pem_private_key(private_key.encode(), password=None, backend=default_backend())
        else:
            self.private_key = ec.generate_private_key(ec.SECP256R1(), default_backend())
        self.public_key = self.private_key.public_key()
        self.address = self.generate_address()
        self.balances = defaultdict(Fraction)
        self.imported_tokens = set()

    def generate_address(self):
        public_bytes = self.public_key.public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
        address_hash = hashlib.sha256(public_bytes).digest()
        return f"0xBrainers{base58.b58encode(address_hash).decode()[:34]}"

    def get_private_key(self):
        return self.private_key.private_bytes(
            Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()
        ).decode()

    def sign_transaction(self, transaction_data: str):
        signature = self.private_key.sign(
            transaction_data.encode(),
            ec.ECDSA(hashes.SHA256())
        )
        return base58.b58encode(signature).decode()

    def import_token(self, token_address: str):
        self.imported_tokens.add(token_address)

    def to_dict(self):
        return {
            "address": self.address,
            "balances": {token: str(balance) for token, balance in self.balances.items()},
            "imported_tokens": list(self.imported_tokens)
        }

class Validator:
    def __init__(self, address: str, stake: Fraction, is_gift: bool = False):
        self.address = address
        self.stake = stake
        self.is_gift = is_gift
        self.last_block_validated = 0
        self.reputation = Fraction(1)
        self.is_active = True
        self.total_rewards = Fraction(0)
        self.performance_history = []

    def update_reputation(self, performance: Fraction):
        self.reputation = (self.reputation * Fraction(9, 10)) + (performance * Fraction(1, 10))
        self.performance_history.append((time.time(), performance))
        if len(self.performance_history) > 1000:
            self.performance_history.pop(0)

    def add_reward(self, amount: Fraction):
        self.total_rewards += amount

    def to_dict(self):
        return {
            "address": self.address,
            "stake": str(self.stake),
            "is_gift": self.is_gift,
            "last_block_validated": self.last_block_validated,
            "reputation": str(self.reputation),
            "is_active": self.is_active,
            "total_rewards": str(self.total_rewards),
            "average_performance": str(sum(p[1] for p in self.performance_history) / len(self.performance_history)) if self.performance_history else "0"
        }

class SmartContract:
    def __init__(self, address: str, creator: str, code: str, abi: Dict[str, Any]):
        self.address = address
        self.creator = creator
        self.code = code
        self.abi = abi
        self.storage = {}

    async def execute(self, method: str, params: Dict[str, Any], context: 'ExecutionContext') -> Any:
        if method not in self.abi:
            raise ValueError(f"Method {method} not found in contract ABI")

        global_vars = {
            "storage": self.storage,
            "context": context,
            "params": params
        }
        exec(self.code, global_vars)
        result = await global_vars[method](**params)
        return result

    def to_dict(self):
        return {
            "address": self.address,
            "creator": self.creator,
            "abi": self.abi
        }

class DEX:
    def __init__(self, blockchain):
        self.blockchain = blockchain
        self.liquidity_pools = defaultdict(lambda: {'BRAINERS': Fraction(0), 'TOKEN': Fraction(0)})
        self.orders = defaultdict(list)
        self.trading_start_times = {}
        self.chat_messages = defaultdict(list)
        self.trading_pairs = set()
        self.fee_percentage = Fraction(3, 1000)  # 0.3% fee

    async def add_liquidity(self, token_address: str, brainers_amount: Fraction, token_amount: Fraction, provider: str, lock_time: int):
        if self.liquidity_pools[token_address]['BRAINERS'] + brainers_amount < MIN_LIQUIDITY_DEX:
            return False, "Insufficient liquidity"

        self.liquidity_pools[token_address]['BRAINERS'] += brainers_amount
        self.liquidity_pools[token_address]['TOKEN'] += token_amount

        if token_address not in self.trading_start_times:
            self.trading_start_times[token_address] = time.time() + 24 * 60 * 60  # Start trading after 24 hours

        self.trading_pairs.add((token_address, 'BRAINERS'))

        # Create a liquidity provider transaction
        lp_tx = Transaction(
            sender=provider,
            recipient=self.blockchain.dex_address,
            amount=brainers_amount,
            transaction_type="add_liquidity",
            fee=self.blockchain.calculate_transaction_fee(brainers_amount),
            data={
                'token_address': token_address,
                'token_amount': str(token_amount),
                'lock_time': lock_time
            }
        )
        await self.blockchain.add_transaction(lp_tx)

        return True, "Liquidity added successfully"

    async def remove_liquidity(self, token_address: str, liquidity_amount: Fraction, provider: str):
        pool = self.liquidity_pools[token_address]
        total_liquidity = pool['BRAINERS'] + pool['TOKEN']
        
        brainers_to_return = (liquidity_amount / total_liquidity) * pool['BRAINERS']
        tokens_to_return = (liquidity_amount / total_liquidity) * pool['TOKEN']

        pool['BRAINERS'] -= brainers_to_return
        pool['TOKEN'] -= tokens_to_return

        # Create a liquidity removal transaction
        remove_lp_tx = Transaction(
            sender=self.blockchain.dex_address,
            recipient=provider,
            amount=brainers_to_return,
            transaction_type="remove_liquidity",
            fee=Fraction(0),
            data={
                'token_address': token_address,
                'token_amount': str(tokens_to_return)
            }
        )
        await self.blockchain.add_transaction(remove_lp_tx)

        return True, f"Removed {brainers_to_return} BRAINERS and {tokens_to_return} tokens from liquidity pool"

    async def place_order(self, token_address: str, order_type: str, amount: Fraction, price: Fraction, trader: str):
        if time.time() < self.trading_start_times.get(token_address, 0):
            return False, "Trading has not started for this token"

        order = {
            'trader': trader,
            'type': order_type,
            'amount': amount,
            'price': price,
            'timestamp': time.time()
        }
        self.orders[token_address].append(order)

        # Create an order placement transaction
        order_tx = Transaction(
            sender=trader,
            recipient=self.blockchain.dex_address,
            amount=Fraction(0),
            transaction_type="place_order",
            fee=self.blockchain.calculate_transaction_fee(Fraction(0)),
            data={
                'token_address': token_address,
                'order_type': order_type,
                'amount': str(amount),
                'price': str(price)
            }
        )
        await self.blockchain.add_transaction(order_tx)

        await self.match_orders(token_address)
        return True, "Order placed successfully"

    async def match_orders(self, token_address: str):
        buy_orders = sorted([o for o in self.orders[token_address] if o['type'] == 'buy'], key=lambda x: x['price'], reverse=True)
        sell_orders = sorted([o for o in self.orders[token_address] if o['type'] == 'sell'], key=lambda x: x['price'])

        while buy_orders and sell_orders and buy_orders[0]['price'] >= sell_orders[0]['price']:
            buy_order = buy_orders[0]
            sell_order = sell_orders[0]

            trade_price = (buy_order['price'] + sell_order['price']) / 2
            trade_amount = min(buy_order['amount'], sell_order['amount'])

            # Execute the trade
            await self.execute_trade(token_address, buy_order['trader'], sell_order['trader'], trade_amount, trade_price)

            # Update orders
            buy_order['amount'] -= trade_amount
            sell_order['amount'] -= trade_amount

            if buy_order['amount'] == 0:
                buy_orders.pop(0)
            if sell_order['amount'] == 0:
                sell_orders.pop(0)

        # Update the order book
        self.orders[token_address] = buy_orders + sell_orders

    async def execute_trade(self, token_address: str, buyer: str, seller: str, amount: Fraction, price: Fraction):
        brainers_amount = amount * price
        fee = brainers_amount * self.fee_percentage

        # Create a trade execution transaction
        trade_tx = Transaction(
            sender=self.blockchain.dex_address,
            recipient=self.blockchain.dex_address,
            amount=Fraction(0),
            transaction_type="execute_trade",
            fee=fee,
            data={
                'token_address': token_address,
                'buyer': buyer,
                'seller': seller,
                'token_amount': str(amount),
                'brainers_amount': str(brainers_amount)
            }
        )
        await self.blockchain.add_transaction(trade_tx)

        # Update balances (this should be done in the blockchain's apply_transaction method)
        self.blockchain.accounts[buyer][token_address] += amount
        self.blockchain.accounts[buyer]['BRAINERS'] -= brainers_amount + fee/2
        self.blockchain.accounts[seller][token_address] -= amount
        self.blockchain.accounts[seller]['BRAINERS'] += brainers_amount - fee/2

    async def add_chat_message(self, token_address: str, sender: str, message: str):
        chat_tx = Transaction(
            sender=sender,
            recipient=self.blockchain.dex_address,
            amount=Fraction(0),
            transaction_type="chat_message",
            fee=self.blockchain.calculate_transaction_fee(Fraction(0)),
            data={
                'token_address': token_address,
                'message': message
            }
        )
        await self.blockchain.add_transaction(chat_tx)

        self.chat_messages[token_address].append({
            'sender': sender,
            'message': message,
            'timestamp': time.time()
        })

    def get_order_book(self, token_address: str):
        return {
            'buy_orders': [o for o in self.orders[token_address] if o['type'] == 'buy'],
            'sell_orders': [o for o in self.orders[token_address] if o['type'] == 'sell']
        }

    def get_chat_messages(self, token_address: str, limit: int = 100):
        return self.chat_messages[token_address][-limit:]

    def get_liquidity_pool_info(self, token_address: str):
        pool = self.liquidity_pools[token_address]
        return {
            'brainers': str(pool['BRAINERS']),
            'token': str(pool['TOKEN']),
            'total_liquidity': str(pool['BRAINERS'] + pool['TOKEN'])
        }

class TTF:
    def __init__(self, blockchain):
        self.blockchain = blockchain
        self.futures = {}
        self.positions = defaultdict(list)
        self.oracle_prices = {}
        self.liquidation_threshold = Fraction(80, 100)  # 80% of collateral

    async def create_future(self, token_address: str, creator: str):
        if self.blockchain.dex.liquidity_pools[token_address]['BRAINERS'] < MIN_LIQUIDITY_TTF:
            return False, "Insufficient liquidity for TTF creation"

        future_id = f"TTF-{token_address}-{int(time.time())}"
        self.futures[future_id] = {
            'token_address': token_address,
            'creator': creator,
            'creation_time': time.time()
        }

        create_ttf_tx = Transaction(
            sender=creator,
            recipient=self.blockchain.ttf_address,
            amount=Fraction(0),
            transaction_type="create_ttf",
            fee=self.blockchain.calculate_transaction_fee(Fraction(0)),
            data={
                'future_id': future_id,
                'token_address': token_address
            }
        )
        await self.blockchain.add_transaction(create_ttf_tx)

        return True, future_id

    async def open_position(self, future_id: str, trader: str, position_type: str, amount: Fraction, leverage: int):
        if future_id not in self.futures:
            return False, "Future does not exist"

        token_address = self.futures[future_id]['token_address']
        current_price = self.get_oracle_price(token_address)
        
        collateral = amount / leverage
        if self.blockchain.accounts[trader]['BRAINERS'] < collateral:
            return False, "Insufficient collateral"

        position = {
            'trader': trader,
            'type': position_type,
            'amount': amount,
            'leverage': leverage,
            'collateral': collateral,
            'open_price': current_price,
            'open_time': time.time()
        }
        self.positions[future_id].append(position)

        open_position_tx = Transaction(
            sender=trader,
            recipient=self.blockchain.ttf_address,
            amount=collateral,
            transaction_type="open_ttf_position",
            fee=self.blockchain.calculate_transaction_fee(collateral),
            data={
                'future_id': future_id,
                'position_type': position_type,
                'amount': str(amount),
                'leverage': leverage
            }
        )
        await self.blockchain.add_transaction(open_position_tx)

        return True, "Position opened successfully"

    async def close_position(self, future_id: str, position_index: int, trader: str):
        if future_id not in self.futures or position_index >= len(self.positions[future_id]):
            return False, "Position does not exist"

        position = self.positions[future_id][position_index]
        if position['trader'] != trader:
            return False, "Not the position owner"

        token_address = self.futures[future_id]['token_address']
        close_price = self.get_oracle_price(token_address)
        pnl = self.calculate_pnl(position, close_price)

        close_position_tx = Transaction(
            sender=self.blockchain.ttf_address,
            recipient=trader,
            amount=position['collateral'] + pnl,
            transaction_type="close_ttf_position",
            fee=self.blockchain.calculate_transaction_fee(Fraction(0)),
            data={
                'future_id': future_id,
                'position_index': position_index,
                'close_price': str(close_price),
                'pnl': str(pnl)
            }
        )
        await self.blockchain.add_transaction(close_position_tx)

        del self.positions[future_id][position_index]
        return True, f"Position closed. PnL: {pnl}"

    def get_oracle_price(self, token_address: str):
        # In a real implementation, this would fetch the price from an oracle
        return self.oracle_prices.get(token_address, Fraction(1))

    def calculate_pnl(self, position, close_price):
        price_diff = close_price - position['open_price']
        if position['type'] == 'short':
            price_diff = -price_diff
        return position['amount'] * price_diff * position['leverage']

    async def update_oracle_price(self, token_address: str, new_price: Fraction):
        self.oracle_prices[token_address] = new_price
        await self.check_liquidations(token_address)

    async def check_liquidations(self, token_address: str):
        current_price = self.get_oracle_price(token_address)
        for future_id, positions in self.positions.items():
            if self.futures[future_id]['token_address'] == token_address:
                for i, position in enumerate(positions):
                    if self.should_liquidate(position, current_price):
                        await self.liquidate_position(future_id, i)

    def should_liquidate(self, position, current_price):
        pnl = self.calculate_pnl(position, current_price)
        return pnl <= -position['collateral'] * self.liquidation_threshold

    async def liquidate_position(self, future_id: str, position_index: int):
        position = self.positions[future_id][position_index]
        token_address = self.futures[future_id]['token_address']
        current_price = self.get_oracle_price(token_address)
        pnl = self.calculate_pnl(position, current_price)

        liquidation_tx = Transaction(
            sender=self.blockchain.ttf_address,
            recipient=self.blockchain.ttf_address,  # Liquidation pool or insurance fund could be the recipient
            amount=position['collateral'] + pnl,
            transaction_type="liquidate_ttf_position",
            fee=Fraction(0),
            data={
                'future_id': future_id,
                'position_index': position_index,
                'liquidation_price': str(current_price),
                'pnl': str(pnl)
            }
        )
        await self.blockchain.add_transaction(liquidation_tx)

        del self.positions[future_id][position_index]

class TUV:
    def __init__(self, blockchain):
        self.blockchain = blockchain
        self.tuvs = {}

    async def create_tuv(self, creator: str, name: str, image_url: str, token_address: str, token_amount: Fraction, lock_period: int):
        tuv_id = f"TUV-{hashlib.sha256(f'{creator}{name}{time.time()}'.encode()).hexdigest()[:16]}"
        self.tuvs[tuv_id] = {
            'creator': creator,
            'owner': creator,
            'name': name,
            'image_url': image_url,
            'token_address': token_address,
            'token_amount': token_amount,
            'lock_period': lock_period,
            'creation_time': time.time()
        }

        create_tuv_tx = Transaction(
            sender=creator,
            recipient=self.blockchain.tuv_address,
            amount=token_amount,
            transaction_type="create_tuv",
            fee=self.blockchain.calculate_transaction_fee(token_amount),
            data={
                'tuv_id': tuv_id,
                'name': name,
                'image_url': image_url,
                'token_address': token_address,
                'lock_period': lock_period
            }
        )
        await self.blockchain.add_transaction(create_tuv_tx)

        return tuv_id

    async def transfer_tuv(self, tuv_id: str, from_address: str, to_address: str):
        if tuv_id not in self.tuvs or self.tuvs[tuv_id]['owner'] != from_address:
            return False, "TUV does not exist or not owned by sender"

        self.tuvs[tuv_id]['owner'] = to_address

        transfer_tuv_tx = Transaction(
            sender=from_address,
            recipient=to_address,
            amount=Fraction(0),
            transaction_type="transfer_tuv",
            fee=self.blockchain.calculate_transaction_fee(Fraction(0)),
            data={'tuv_id': tuv_id}
        )
        await self.blockchain.add_transaction(transfer_tuv_tx)

        return True, "TUV transferred successfully"

    async def claim_tuv(self, tuv_id: str, claimer: str):
        if tuv_id not in self.tuvs or self.tuvs[tuv_id]['owner'] != claimer:
            return False, "TUV does not exist or not owned by claimer"

        tuv = self.tuvs[tuv_id]
        if time.time() < tuv['creation_time'] + tuv['lock_period']:
            return False, "Lock period not expired"

        claim_tuv_tx = Transaction(
            sender=self.blockchain.tuv_address,
            recipient=claimer,
            amount=tuv['token_amount'],
            transaction_type="claim_tuv",
            fee=Fraction(0),
            data={
                'tuv_id': tuv_id,
                'token_address': tuv['token_address']
            }
        )
        await self.blockchain.add_transaction(claim_tuv_tx)

        del self.tuvs[tuv_id]
        return True, "TUV claimed successfully"

    def get_tuv_info(self, tuv_id: str):
        if tuv_id not in self.tuvs:
            return None
        tuv = self.tuvs[tuv_id]
        return {
            'id': tuv_id,
            'creator': tuv['creator'],
            'owner': tuv['owner'],
            'name': tuv['name'],
            'image_url': tuv['image_url'],
            'token_address': tuv['token_address'],
            'token_amount': str(tuv['token_amount']),
            'lock_period': tuv['lock_period'],
            'creation_time': tuv['creation_time'],
            'claimable': time.time() >= tuv['creation_time'] + tuv['lock_period']
        }

class Blockchain:
    def __init__(self, total_supply: Fraction):
        self.chain = []
        self.pending_transactions = []
        self.accounts = defaultdict(lambda: defaultdict(Fraction))
        self.tokens = {}
        self.validators = {}
        self.smart_contracts = {}
        self.total_supply = total_supply
        self.dex = DEX(self)
        self.ttf = TTF(self)
        self.tuv = TUV(self)
        self.dex_address = "0xBrainersDEX"
        self.ttf_address = "0xBrainersTTF"
        self.tuv_address = "0xBrainersTUV"
        self.min_stake = Fraction(10000, 1)
        self.block_time = TARGET_BLOCK_TIME
        self.db_connection = None
        self.mempool = []
        self.state_root = None

    async def initialize_database(self):
        self.db_connection = await aiosqlite.connect('brainers_blockchain.db')
        await self.db_connection.execute('''
            CREATE TABLE IF NOT EXISTS blocks (
                hash TEXT PRIMARY KEY,
                data TEXT
            )
        ''')
        await self.db_connection.execute('''
            CREATE TABLE IF NOT EXISTS transactions (
                hash TEXT PRIMARY KEY,
                block_hash TEXT,
                data TEXT
            )
        ''')
        await self.db_connection.execute('''
            CREATE TABLE IF NOT EXISTS accounts (
                address TEXT PRIMARY KEY,
                data TEXT
            )
        ''')
        await self.db_connection.execute('''
            CREATE TABLE IF NOT EXISTS tokens (
                address TEXT PRIMARY KEY,
                data TEXT
            )
        ''')
        await self.db_connection.execute('''
            CREATE TABLE IF NOT EXISTS validators (
                address TEXT PRIMARY KEY,
                data TEXT
            )
        ''')
        await self.db_connection.execute('''
            CREATE TABLE IF NOT EXISTS smart_contracts (
                address TEXT PRIMARY KEY,
                data TEXT
            )
        ''')
        await self.db_connection.commit()

    async def create_genesis_block(self):
        genesis_transactions = self.create_initial_distribution()
        genesis_block = Block(
            index=0,
            transactions=genesis_transactions,
            timestamp=time.time(),
            previous_hash="0" * 64,
            validator="0" * 40  # Genesis block has no validator
        )
        self.chain.append(genesis_block)
        await self.save_block(genesis_block)
        self.state_root = self.calculate_state_root()
        logger.info("Genesis block created")

    def create_initial_distribution(self) -> List[Transaction]:
        distributions = [
            ("Rezerva", Fraction(742, 10000)),
            ("Lichiditate", Fraction(19, 100)),
            ("Rez_stable_coin", Fraction(19, 100)),
            ("Investitori_P", Fraction(20, 100)),
            ("Garantie", Fraction(19, 100)),
            ("Farming", Fraction(558, 10000))
        ]
        
        transactions = []
        for category, percentage in distributions:
            amount = self.total_supply * percentage
            wallet = Wallet()
            transaction = Transaction(
                sender="0" * 40,  # Genesis transaction
                recipient=wallet.address,
                amount=amount,
                transaction_type="genesis",
                fee=Fraction(0),
                signature=""  # Genesis transactions are not signed
            )
            transactions.append(transaction)
            self.accounts[wallet.address]["BRAINERS"] += amount
            logger.info(f"Allocated {amount} BRAINERS to {category} wallet: {wallet.address}")
            logger.info(f"{category} Wallet Private Key: {wallet.get_private_key()}")
        
        return transactions

    def calculate_transaction_fee(self, amount: Fraction) -> Fraction:
        base_fee = MIN_FEE
        fee_multiplier = Fraction(3, 2) ** (len(self.pending_transactions) // 1000)
        fee = min(max(base_fee * fee_multiplier, MIN_FEE), MAX_FEE)
        return fee

    async def add_transaction(self, transaction: Transaction) -> bool:
        if transaction.transaction_type != "genesis":
            if self.accounts[transaction.sender][transaction.data.get('token', 'BRAINERS')] < transaction.amount + transaction.fee:
                logger.error(f"Insufficient balance for sender {transaction.sender}")
                return False

        if not self.verify_transaction(transaction):
            logger.error(f"Invalid transaction signature for transaction {transaction.hash}")
            return False

        self.mempool.append(transaction)
        return True

    def verify_transaction(self, transaction: Transaction) -> bool:
        if transaction.transaction_type == "genesis":
            return True
        try:
            public_key = ec.derive_public_key_from_private(transaction.sender)
            return transaction.verify_signature(public_key)
        except:
            return False

    async def create_block(self) -> Optional[Block]:
        validator = self.select_validator()
        if not validator:
            logger.error(f"No active validators available")
            return None

        transactions = self.mempool[:MAX_TRANSACTIONS_PER_BLOCK]
        new_block = Block(
            index=len(self.chain),
            transactions=transactions,
            timestamp=time.time(),
            previous_hash=self.chain[-1].hash if self.chain else "0" * 64,
            validator=validator.address
        )

        for tx in transactions:
            await self.apply_transaction(tx)

        self.chain.append(new_block)
        await self.save_block(new_block)
        self.mempool = self.mempool[MAX_TRANSACTIONS_PER_BLOCK:]

        # Reward for validator
        reward_tx = Transaction(
            sender="0" * 40,
            recipient=validator.address,
            amount=self.calculate_block_reward(),
            transaction_type="reward",
            fee=Fraction(0),
            signature=""
        )
        await self.apply_transaction(reward_tx)

        validator.update_reputation(Fraction(1))  # Assume successful validation
        validator.last_block_validated = new_block.index

        self.state_root = self.calculate_state_root()

        return new_block

    def select_validator(self) -> Optional[Validator]:
        eligible_validators = [v for v in self.validators.values() if v.stake >= self.min_stake and v.is_active]
        
        if not eligible_validators:
            return None

        total_stake = sum(v.stake * v.reputation for v in eligible_validators)
        selection_point = random.uniform(0, float(total_stake))
        current_point = Fraction(0)

        for validator in eligible_validators:
            current_point += validator.stake * validator.reputation
            if current_point >= selection_point:
                return validator

        return eligible_validators[-1]  # Fallback to last validator if something goes wrong

    async def apply_transaction(self, transaction: Transaction):
        token = transaction.data.get('token', 'BRAINERS')
        if transaction.transaction_type in ['transfer', 'genesis', 'reward']:
            self.accounts[transaction.sender][token] -= transaction.amount + transaction.fee
            self.accounts[transaction.recipient][token] += transaction.amount
        elif transaction.transaction_type == 'create_token':
            new_token = Token(
                name=transaction.data['name'],
                symbol=transaction.data['symbol'],
                total_supply=transaction.amount,
                creator=transaction.sender,
                is_minable=transaction.data.get('is_minable', False)
            )
            self.tokens[new_token.address] = new_token
            self.accounts[transaction.sender][new_token.address] = transaction.amount
        elif transaction.transaction_type == 'stake':
            self.accounts[transaction.sender]['BRAINERS'] -= transaction.amount + transaction.fee
            if transaction.sender not in self.validators:
                self.validators[transaction.sender] = Validator(transaction.sender, transaction.amount)
            else:
                self.validators[transaction.sender].stake += transaction.amount
        elif transaction.transaction_type == 'unstake':
            if transaction.sender in self.validators:
                self.validators[transaction.sender].stake -= transaction.amount
                self.accounts[transaction.sender]['BRAINERS'] += transaction.amount - transaction.fee
                if self.validators[transaction.sender].stake < self.min_stake:
                    self.validators[transaction.sender].is_active = False
        elif transaction.transaction_type == 'gift_validator':
            self.accounts[transaction.sender]['BRAINERS'] -= GIFT_VALIDATOR_BURN + transaction.fee
            self.validators[transaction.recipient] = Validator(transaction.recipient, GIFT_VALIDATOR_BURN, is_gift=True)
        elif transaction.transaction_type == 'burn':
            self.accounts[transaction.sender][token] -= transaction.amount + transaction.fee
        elif transaction.transaction_type == 'execute_smart_contract':
            await self.execute_smart_contract(transaction)
        elif transaction.transaction_type == 'add_liquidity':
            await self.dex.add_liquidity(
                transaction.data['token_address'],
                transaction.amount,
                Fraction(transaction.data['token_amount']),
                transaction.sender,
                transaction.data['lock_time']
            )
        elif transaction.transaction_type == 'remove_liquidity':
            await self.dex.remove_liquidity(
                transaction.data['token_address'],
                transaction.amount,
                transaction.sender
            )
        elif transaction.transaction_type == 'place_order':
            await self.dex.place_order(
                transaction.data['token_address'],
                transaction.data['order_type'],
                transaction.amount,
                Fraction(transaction.data['price']),
                transaction.sender
            )
        elif transaction.transaction_type == 'create_ttf':
            await self.ttf.create_future(
                transaction.data['token_address'],
                transaction.sender
            )
        elif transaction.transaction_type == 'open_ttf_position':
            await self.ttf.open_position(
                transaction.data['future_id'],
                transaction.sender,
                transaction.data['position_type'],
                transaction.amount,
                transaction.data['leverage']
            )
        elif transaction.transaction_type == 'close_ttf_position':
            await self.ttf.close_position(
                transaction.data['future_id'],
                transaction.data['position_index'],
                transaction.sender
            )
        elif transaction.transaction_type == 'create_tuv':
            await self.tuv.create_tuv(
                transaction.sender,
                transaction.data['name'],
                transaction.data['image_url'],
                transaction.data['token_address'],
                transaction.amount,
                transaction.data['lock_period']
            )
        elif transaction.transaction_type == 'transfer_tuv':
            await self.tuv.transfer_tuv(
                transaction.data['tuv_id'],
                transaction.sender,
                transaction.recipient
            )
        elif transaction.transaction_type == 'claim_tuv':
            await self.tuv.claim_tuv(
                transaction.data['tuv_id'],
                transaction.sender
            )

    async def execute_smart_contract(self, transaction: Transaction):
        contract = self.smart_contracts.get(transaction.recipient)
        if not contract:
            logger.error(f"Smart contract not found: {transaction.recipient}")
            return

        context = ExecutionContext(self, transaction.sender)
        try:
            result = await contract.execute(transaction.data['method'], transaction.data['params'], context)
            logger.info(f"Smart contract executed: {result}")
        except Exception as e:
            logger.error(f"Smart contract execution failed: {str(e)}")

    async def create_smart_contract(self, creator: str, code: str, abi: Dict[str, Any]) -> str:
        contract_address = f"0xBrainers{''.join(random.choices(string.ascii_lowercase + string.digits, k=6))}"
        new_contract = SmartContract(contract_address, creator, code, abi)
        self.smart_contracts[contract_address] = new_contract
        
        create_contract_tx = Transaction(
            sender=creator,
            recipient=contract_address,
            amount=Fraction(0),
            transaction_type="create_smart_contract",
            fee=self.calculate_transaction_fee(Fraction(0)),
            data={
                'code': code,
                'abi': abi
            }
        )
        await self.add_transaction(create_contract_tx)

        return contract_address

    async def create_token(self, creator: str, name: str, symbol: str, total_supply: Fraction, is_minable: bool = False) -> str:
        create_token_tx = Transaction(
            sender=creator,
            recipient="0" * 40,  # Token creation doesn't have a recipient
            amount=total_supply,
            transaction_type="create_token",
            fee=self.calculate_transaction_fee(total_supply),
            data={
                'name': name,
                'symbol': symbol,
                'is_minable': is_minable
            }
        )
        success = await self.add_transaction(create_token_tx)
        if success:
            new_token = Token(name, symbol, total_supply, creator, is_minable)
            self.tokens[new_token.address] = new_token
            return new_token.address
        return None

    async def stake_tokens(self, staker: str, amount: Fraction) -> bool:
        stake_tx = Transaction(
            sender=staker,
            recipient=staker,
            amount=amount,
            transaction_type="stake",
            fee=self.calculate_transaction_fee(amount),
            data={'token': 'BRAINERS'}
        )
        return await self.add_transaction(stake_tx)

    async def unstake_tokens(self, staker: str, amount: Fraction) -> bool:
        if staker not in self.validators or self.validators[staker].stake < amount:
            return False
        unstake_tx = Transaction(
            sender=staker,
            recipient=staker,
            amount=amount,
            transaction_type="unstake",
            fee=self.calculate_transaction_fee(amount),
            data={'token': 'BRAINERS'}
        )
        return await self.add_transaction(unstake_tx)

    async def gift_validator(self, gifter: str, recipient: str) -> bool:
        if self.accounts[gifter]['BRAINERS'] < GIFT_VALIDATOR_BURN:
            return False
        gift_tx = Transaction(
            sender=gifter,
            recipient=recipient,
            amount=GIFT_VALIDATOR_BURN,
            transaction_type="gift_validator",
            fee=self.calculate_transaction_fee(GIFT_VALIDATOR_BURN),
            data={'token': 'BRAINERS'}
        )
        return await self.add_transaction(gift_tx)

    async def burn_tokens(self, burner: str, amount: Fraction, token: str = 'BRAINERS') -> bool:
        burn_tx = Transaction(
            sender=burner,
            recipient="0" * 40,  # Burn address
            amount=amount,
            transaction_type="burn",
            fee=self.calculate_transaction_fee(amount),
            data={'token': token}
        )
        return await self.add_transaction(burn_tx)

    async def save_block(self, block: Block):
        await self.db_connection.execute(
            "INSERT OR REPLACE INTO blocks (hash, data) VALUES (?, ?)",
            (block.hash, json.dumps(block.to_dict(), cls=BrainersJSONEncoder))
        )
        for tx in block.transactions:
            await self.db_connection.execute(
                "INSERT OR REPLACE INTO transactions (hash, block_hash, data) VALUES (?, ?, ?)",
                (tx.hash, block.hash, json.dumps(tx.to_dict(), cls=BrainersJSONEncoder))
            )
        await self.db_connection.commit()

    async def get_block(self, block_hash: str) -> Optional[Block]:
        async with self.db_connection.execute("SELECT data FROM blocks WHERE hash = ?", (block_hash,)) as cursor:
            result = await cursor.fetchone()
            if result:
                return Block.from_dict(json.loads(result[0]))
        return None

    async def get_transaction(self, tx_hash: str) -> Optional[Transaction]:
        async with self.db_connection.execute("SELECT data FROM transactions WHERE hash = ?", (tx_hash,)) as cursor:
            result = await cursor.fetchone()
            if result:
                return Transaction.from_dict(json.loads(result[0]))
        return None

    def calculate_block_reward(self) -> Fraction:
        # Implementare simplă, poate fi ajustată pentru a include halvings sau alte mecanisme
        return Fraction(1, 1)

    async def get_balance(self, address: str, token: str = 'BRAINERS') -> Fraction:
        return self.accounts[address][token]

    async def get_token_info(self, token_address: str) -> Optional[Dict]:
        token = self.tokens.get(token_address)
        if token:
            return token.to_dict()
        return None

    async def get_validator_info(self, address: str) -> Optional[Dict]:
        validator = self.validators.get(address)
        if validator:
            return validator.to_dict()
        return None

    def calculate_state_root(self) -> str:
        state = {
            'accounts': self.accounts,
            'validators': self.validators,
            'tokens': self.tokens,
            'smart_contracts': self.smart_contracts
        }
        return hashlib.sha256(json.dumps(state, sort_keys=True, cls=BrainersJSONEncoder).encode()).hexdigest()

    async def sync_with_peer(self, peer_blocks: List[Dict]):
        for block_data in peer_blocks:
            block = Block.from_dict(block_data)
            if block.index > len(self.chain):
                # Verificăm validitatea blocului
                if self.is_valid_block(block):
                    self.chain.append(block)
                    for tx in block.transactions:
                        await self.apply_transaction(tx)
                    await self.save_block(block)
                else:
                    logger.warning(f"Invalid block received: {block.hash}")

    def is_valid_block(self, block: Block) -> bool:
        # Implementare simplificată a validării blocului
        if block.index > 0:
            previous_block = self.chain[block.index - 1]
            if block.previous_hash != previous_block.hash:
                return False
        for tx in block.transactions:
            if not self.verify_transaction(tx):
                return False
        return True

    async def get_blockchain_state(self) -> Dict:
        return {
            'chain_length': len(self.chain),
            'last_block_hash': self.chain[-1].hash if self.chain else None,
            'state_root': self.state_root,
            'pending_transactions': len(self.mempool),
            'active_validators': sum(1 for v in self.validators.values() if v.is_active),
            'total_supply': str(self.total_supply),
            'circulating_supply': str(sum(self.accounts[addr]['BRAINERS'] for addr in self.accounts))
        }

    async def reindex_blockchain(self):
        # Reindexarea completă a blockchain-ului, utilă pentru recuperare sau verificări
        self.accounts = defaultdict(lambda: defaultdict(Fraction))
        self.validators = {}
        self.tokens = {}
        self.smart_contracts = {}

        for block in self.chain:
            for tx in block.transactions:
                await self.apply_transaction(tx)

        self.state_root = self.calculate_state_root()

    async def create_custom_token(self, creator: str, name: str, symbol: str, total_supply: Fraction, is_minable: bool, attributes: Dict[str, Any]) -> str:
        token_address = await self.create_token(creator, name, symbol, total_supply, is_minable)
        if token_address:
            self.tokens[token_address].attributes = attributes
        return token_address

    async def transfer_tokens(self, sender: str, recipient: str, amount: Fraction, token: str = 'BRAINERS') -> bool:
        transfer_tx = Transaction(
            sender=sender,
            recipient=recipient,
            amount=amount,
            transaction_type="transfer",
            fee=self.calculate_transaction_fee(amount),
            data={'token': token}
        )
        return await self.add_transaction(transfer_tx)

    async def get_transaction_history(self, address: str) -> List[Dict]:
        history = []
        for block in self.chain:
            for tx in block.transactions:
                if tx.sender == address or tx.recipient == address:
                    history.append(tx.to_dict())
        return history

    async def get_smart_contract_info(self, contract_address: str) -> Optional[Dict]:
        contract = self.smart_contracts.get(contract_address)
        if contract:
            return contract.to_dict()
        return None

    async def update_validator_nodes(self):
        # Această metodă ar putea fi apelată periodic pentru a actualiza lista de noduri validator
        active_validators = [v for v in self.validators.values() if v.is_active and v.stake >= self.min_stake]
        # Aici s-ar putea implementa logica pentru a distribui informațiile despre nodurile active către toți participanții
        logger.info(f"Active validators updated. Count: {len(active_validators)}")

    async def process_mempool(self):
        # Procesează tranzacțiile din mempool și le include în blocuri
        while self.mempool:
            new_block = await self.create_block()
            if new_block:
                logger.info(f"New block created: {new_block.hash}")
            else:
                break
        logger.info(f"Mempool processing complete. Remaining transactions: {len(self.mempool)}")

class BlockchainNode:
    def __init__(self, host: str, port: int, blockchain: Blockchain, use_ssl: bool = False):
        self.host = host
        self.port = port
        self.blockchain = blockchain
        self.peers = set()
        self.ssl_context = None
        self.use_ssl = use_ssl

    async def start(self):
        if self.use_ssl:
            try:
                self.ssl_context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
                self.ssl_context.load_cert_chain('path/to/fullchain.pem', 'path/to/privkey.pem')
            except FileNotFoundError:
                logger.warning("SSL certificate files not found. Running without SSL.")
                self.use_ssl = False
        
        server = await websockets.serve(
            self.handle_connection, self.host, self.port, ssl=self.ssl_context if self.use_ssl else None
        )

        await self.blockchain.initialize_database()
        await self.blockchain.create_genesis_block()
        asyncio.create_task(self.block_creation_loop())
        asyncio.create_task(self.peer_discovery_loop())
        asyncio.create_task(self.mempool_processing_loop())
        asyncio.create_task(self.validator_update_loop())

        protocol = "wss" if self.use_ssl else "ws"
        logger.info(f"Node started on {protocol}://{self.host}:{self.port}")
        await server.wait_closed()

    async def handle_connection(self, websocket, path):
        peer = f"{websocket.remote_address[0]}:{websocket.remote_address[1]}"
        self.peers.add(peer)
        try:
            async for message in websocket:
                await self.process_message(websocket, message)
        finally:
            self.peers.remove(peer)

    async def process_message(self, websocket, message):
        data = json.loads(message)
        if data['type'] == 'new_transaction':
            tx = Transaction.from_dict(data['transaction'])
            success = await self.blockchain.add_transaction(tx)
            await websocket.send(json.dumps({'type': 'transaction_response', 'success': success}, cls=BrainersJSONEncoder))
        elif data['type'] == 'new_block':
            block = Block.from_dict(data['block'])
            if self.blockchain.is_valid_block(block):
                await self.blockchain.sync_with_peer([data['block']])
                await self.broadcast(message, exclude=websocket)
        elif data['type'] == 'get_blockchain_state':
            state = await self.blockchain.get_blockchain_state()
            await websocket.send(json.dumps({'type': 'blockchain_state', 'state': state}, cls=BrainersJSONEncoder))
        elif data['type'] == 'sync_request':
            last_block = data.get('last_block', -1)
            blocks_to_send = [block.to_dict() for block in self.blockchain.chain[last_block+1:]]
            await websocket.send(json.dumps({'type': 'sync_response', 'blocks': blocks_to_send}, cls=BrainersJSONEncoder))

    async def broadcast(self, message, exclude=None):
        for peer in self.peers:
            if peer != exclude:
                try:
                    async with websockets.connect(f'{"wss" if self.use_ssl else "ws"}://{peer}', ssl=self.ssl_context if self.use_ssl else None) as websocket:
                        await websocket.send(message)
                except Exception as e:
                    logger.error(f"Failed to broadcast to {peer}: {str(e)}")

    async def block_creation_loop(self):
        while True:
            new_block = await self.blockchain.create_block()
            if new_block:
                await self.broadcast(json.dumps({'type': 'new_block', 'block': new_block.to_dict()}, cls=BrainersJSONEncoder))
            await asyncio.sleep(self.blockchain.block_time)

    async def peer_discovery_loop(self):
        while True:
            # Implementare simplificată a descoperirii de peer-uri
            known_peers = ['peer1.example.com:8765', 'peer2.example.com:8765']
            for peer in known_peers:
                if peer not in self.peers:
                    try:
                        async with websockets.connect(f'{"wss" if self.use_ssl else "ws"}://{peer}', ssl=self.ssl_context if self.use_ssl else None) as websocket:
                            await websocket.send(json.dumps({'type': 'hello'}))
                            self.peers.add(peer)
                    except Exception as e:
                        logger.error(f"Failed to connect to peer {peer}: {str(e)}")
            await asyncio.sleep(300)  # Verifică la fiecare 5 minute

    async def mempool_processing_loop(self):
        while True:
            await self.blockchain.process_mempool()
            await asyncio.sleep(1)  # Procesează mempool-ul la fiecare secundă

    async def validator_update_loop(self):
        while True:
            await self.blockchain.update_validator_nodes()
            await asyncio.sleep(60)  # Actualizează lista de validatori la fiecare minut

class BlockchainAPI:
    def __init__(self, blockchain: Blockchain):
        self.blockchain = blockchain
        self.app = web.Application()
        self.setup_routes()

    def setup_routes(self):
        self.app.router.add_get('/balance/{address}', self.get_balance)
        self.app.router.add_get('/transaction/{tx_hash}', self.get_transaction)
        self.app.router.add_get('/block/{block_hash}', self.get_block)
        self.app.router.add_post('/transaction', self.create_transaction)
        self.app.router.add_get('/token/{token_address}', self.get_token_info)
        self.app.router.add_get('/validator/{address}', self.get_validator_info)
        self.app.router.add_get('/state', self.get_blockchain_state)
        self.app.router.add_post('/stake', self.stake_tokens)
        self.app.router.add_post('/unstake', self.unstake_tokens)
        self.app.router.add_post('/burn', self.burn_tokens)
        self.app.router.add_post('/create_token', self.create_token)
        self.app.router.add_post('/create_smart_contract', self.create_smart_contract)
        self.app.router.add_post('/execute_smart_contract', self.execute_smart_contract)

    async def get_balance(self, request):
        address = request.match_info['address']
        token = request.query.get('token', 'BRAINERS')
        balance = await self.blockchain.get_balance(address, token)
        return web.json_response({'balance': str(balance)})

    async def unstake_tokens(self, request):
        data = await request.json()
        address = data.get('address')
        amount = Fraction(data.get('amount', 0))

        if not address or amount <= 0:
            return web.json_response({'error': 'Invalid request data'}, status=400)

        success = await self.blockchain.unstake_tokens(address, amount)

        if success:
            return web.json_response({'success': True})
        return web.json_response({'success': False, 'error': 'Unstake failed'}, status=400)
        
        
        
    async def stake_tokens(self, request):
        data = await request.json()
        address = data.get('address')
        amount = Fraction(data.get('amount', 0))

        if not address or amount <= 0:
            return web.json_response({'error': 'Invalid request data'}, status=400)

        success = await self.blockchain.stake_tokens(address, amount)

        if success:
            return web.json_response({'success': True})
        return web.json_response({'success': False, 'error': 'Stake failed'}, status=400)


    async def create_smart_contract(self, request):
        data = await request.json()
        creator = data.get('creator')
        code = data.get('code')
        abi = data.get('abi')

        if not creator or not code or not abi:
            return web.json_response({'error': 'Invalid request data'}, status=400)

        contract_address = await self.blockchain.create_smart_contract(creator, code, abi)

        if contract_address:
            return web.json_response({'success': True, 'contract_address': contract_address})
        return web.json_response({'success': False, 'error': 'Smart contract creation failed'}, status=400)




    async def execute_smart_contract(self, request):
        data = await request.json()
        contract_address = data.get('contract_address')
        method = data.get('method')
        params = data.get('params', {})
        caller = data.get('caller')

        if not contract_address or not method or not caller:
            return web.json_response({'error': 'Invalid request data'}, status=400)

        try:
            result = await self.blockchain.execute_smart_contract(contract_address, method, params, caller)
            return web.json_response({'success': True, 'result': result})
        except Exception as e:
            return web.json_response({'success': False, 'error': str(e)}, status=400)


    async def create_token(self, request):
        data = await request.json()
        creator = data.get('creator')
        name = data.get('name')
        symbol = data.get('symbol')
        total_supply = Fraction(data.get('total_supply', 0))
        is_minable = data.get('is_minable', False)

        if not creator or not name or not symbol or total_supply <= 0:
            return web.json_response({'error': 'Invalid request data'}, status=400)

        token_address = await self.blockchain.create_token(creator, name, symbol, total_supply, is_minable)

        if token_address:
            return web.json_response({'success': True, 'token_address': token_address})
        return web.json_response({'success': False, 'error': 'Token creation failed'}, status=400)







    async def burn_tokens(self, request):
        data = await request.json()
        address = data.get('address')
        amount = Fraction(data.get('amount', 0))
        token = data.get('token', 'BRAINERS')

        if not address or amount <= 0:
            return web.json_response({'error': 'Invalid request data'}, status=400)

        success = await self.blockchain.burn_tokens(address, amount, token)

        if success:
            return web.json_response({'success': True})
        return web.json_response({'success': False, 'error': 'Burn failed'}, status=400)



    async def get_transaction(self, request):
        tx_hash = request.match_info['tx_hash']
        tx = await self.blockchain.get_transaction(tx_hash)
        if tx:
            return web.json_response(tx.to_dict())
        return web.json_response({'error': 'Transaction not found'}, status=404)

    async def get_block(self, request):
        block_hash = request.match_info['block_hash']
        block = await self.blockchain.get_block(block_hash)
        if block:
            return web.json_response(block.to_dict())
        return web.json_response({'error': 'Block not found'}, status=404)

    async def create_transaction(self, request):
        data = await request.json()
        tx = Transaction(
            sender=data['sender'],
            recipient=data['recipient'],
            amount=Fraction(data['amount']),
            transaction_type=data['type'],
            fee=self.blockchain.calculate_transaction_fee(Fraction(data['amount'])),
            data=data.get('data', {})
        )
        success = await self.blockchain.add_transaction(tx)
        return web.json_response({'success': success})

    async def get_token_info(self, request):
        token_address = request.match_info['token_address']
        token_info = await self.blockchain.get_token_info(token_address)
        if token_info:
            return web.json_response(token_info)
        return web.json_response({'error': 'Token not found'}, status=404)

    async def get_validator_info(self, request):
        address = request.match_info['address']
        validator_info = await self.blockchain.get_validator_info(address)
        if validator_info:
            return web.json_response(validator_info)
        return web.json_response({'error': 'Validator not found'}, status=404)

    async def get_blockchain_state(self, request):
        state = await self.blockchain.get_blockchain_state()
        return web.json_response(state)

async def main():
    if len(sys.argv) < 3:
        print("Usage: python brainers_blockchain.py <host> <port>")
        return

    host = sys.argv[1]
    port = int(sys.argv[2])
    
    blockchain = Blockchain(INITIAL_BRAINERS_SUPPLY)
    node = BlockchainNode(host, port, blockchain)
    api = BlockchainAPI(blockchain)
    
    runner = web.AppRunner(api.app)
    await runner.setup()
    site = web.TCPSite(runner, 'localhost', 8080)
    
    await asyncio.gather(
        node.start(),
        site.start()
    )

if __name__ == "__main__":
    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
    asyncio.run(main())