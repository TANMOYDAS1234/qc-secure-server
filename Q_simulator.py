import numpy as np
from qiskit import QuantumCircuit
from qiskit_aer import AerSimulator


class QuantumChannelSimulator:

    def __init__(self, shots=512, trust_threshold=0.35, disturb_prob=0.10):
        self.simulator = AerSimulator()
        self.shots = shots
        self.trust_threshold = trust_threshold
        # Probability that the channel is disturbed/eavesdropped on a given
        # request. This is the single knob for how often a channel is rejected:
        #   0.10 -> ~10% rejected   0.05 -> ~5%   0.00 -> never rejected.
        self.disturb_prob = disturb_prob

    def quantum_measurement(self, theta):
        qc = QuantumCircuit(1, 1)

        # Alice encodes the qubit in a key-derived (theta) basis.
        qc.h(0)
        qc.rz(theta, 0)

        # --- Quantum channel ---
        # An honest, undisturbed channel is left intact. An eavesdropper /
        # tampering event (probability disturb_prob) injects a phase flip that
        # the matched-basis measurement below turns into a full bit error.
        disturbed = np.random.rand() < self.disturb_prob
        if disturbed:
            qc.z(0)

        # Bob measures in Alice's matched basis (undo the encoding). With no
        # disturbance the rotations cancel and the result is deterministically
        # |0> -> bit_error ~ 0 (channel trusted, message passes). A disturbance
        # does not cancel and flips the outcome -> bit_error ~ 1 (rejected).
        # This mirrors BB84: matched bases give no error unless the channel is
        # tampered with, so rejection means "tampering detected", not bad luck.
        qc.rz(-theta, 0)
        qc.h(0)
        qc.measure(0, 0)

        result = self.simulator.run(qc, shots=self.shots).result()
        counts = result.get_counts()

        bit_error = counts.get("1", 0) / self.shots
        return bit_error

    def compute_trust(self, bit_error, randomness):
        # Channel fidelity comes from the actual quantum measurement and
        # dominates the score (0.8). Client-supplied randomness is only a
        # small, capped bonus (0.2) so a peer cannot fabricate trust on a
        # tampered channel: with bit_error = 1, trust <= 0.2 < threshold and the
        # channel is always rejected regardless of the randomness claimed.
        randomness = max(0.0, min(1.0, randomness))
        fidelity = 1.0 - bit_error
        trust = (0.8 * fidelity) + (0.2 * randomness)
        return max(0.0, min(1.0, trust))

    def transmit(self, theta, randomness):
        bit_error = self.quantum_measurement(theta)
        trust = self.compute_trust(bit_error, randomness)

        status = "ACCEPTED" if trust >= self.trust_threshold else "REJECTED"
        return {
            "status": status,
            "bit_error": bit_error,
            "trust": trust
        }
