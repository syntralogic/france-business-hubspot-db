from fbdb.extract.sirene_api import Throttle


def test_throttle_spaces_calls_without_real_sleep():
    t = {"now": 0.0}
    slept = []

    def clock():
        return t["now"]

    def sleep(d):
        slept.append(round(d, 3))
        t["now"] += d

    th = Throttle(2.1, clock=clock, sleep=sleep)
    th.wait()              # first call: no wait
    t["now"] += 0.5
    th.wait()              # 0.5 s elapsed -> must wait 1.6 s
    th.wait()              # immediately again -> full 2.1 s
    assert slept == [1.6, 2.1]
