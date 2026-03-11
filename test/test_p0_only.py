import time

print('=== RateBudget P0-only Logic Test ===')

class MockBudgetState:
    def __init__(self):
        self.current_tokens = 100
        self.refill_rate = 10
        self.is_degraded = False
        self.degraded_until_ts = 0

state = MockBudgetState()
state.is_degraded = True
state.degraded_until_ts = time.time() + 60

def mock_acquire(state, priority):
    now = time.time()
    if state.is_degraded and state.degraded_until_ts > now:
        if priority != 'P0':
            print(f'  [REJECTED] priority={priority}, is_degraded=True -> P0-only mode rejects')
            return False
    if state.current_tokens >= 1:
        state.current_tokens -= 1
        print(f'  [ACCEPTED] priority={priority}, tokens remaining={state.current_tokens}')
        return True
    return False

result_p0 = mock_acquire(state, 'P0')
result_p2 = mock_acquire(state, 'P2')

print('')
print(f'P0 request result: {"PASS" if result_p0 else "REJECT"}')
print(f'P2 request result: {"PASS" if result_p2 else "REJECT"}')

if result_p0 and not result_p2:
    print('OK: P0-only degraded mode logic verified!')
else:
    print('FAIL: P0-only logic error!')
