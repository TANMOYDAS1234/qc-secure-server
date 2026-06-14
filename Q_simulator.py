import numpy as np
from qiskit import QuantumCircuit
from qiskit_aer import AerSimulator


class QuantumChannelSimulator:

    def __init__(self, shots=512, trust_threshold=0.35):
        self.simulator = AerSimulator()
        self.shots = shots
        self.trust_threshold = trust_threshold

    def quantum_measurement(self, theta):
        qc = QuantumCircuit(1, 1)

        # Alice prepares |+> and encodes the key-derived phase.
        qc.h(0)
        qc.rz(theta, 0)

        # Channel disturbance / eavesdropper: ~30% of the time the relative
        # phase is kicked by pi. On its own this is invisible in the Z basis,
        # but the Hadamard below maps the phase onto the measured bit.
        if np.random.rand() < 0.3:
            qc.rz(np.pi, 0)

        # KEY FIX: rotate the phase back into the computational basis so the
        # encoded angle actually affects the outcome. Without this H, rz/z are
        # pure phase and the measurement is a fixed 50/50 coin flip -> theta and
        # the disturbance are unobservable and the trust gate is meaningless.
        # With it, P(measure 1) = sin^2(theta/2)  (or cos^2(theta/2) if kicked).
        qc.h(0)
        qc.measure(0, 0)

        result = self.simulator.run(qc, shots=self.shots).result()
        counts = result.get_counts()

        bit_error = counts.get("1", 0) / self.shots
        return bit_error

    def compute_trust(self, bit_error, randomness):
        # Channel fidelity comes from the actual quantum measurement and
        # dominates the score (0.8). Client-supplied randomness is only a
        # small, capped bonus (0.2) so a peer can no longer fabricate trust on
        # a corrupted channel: with bit_error = 1, trust <= 0.2 < threshold and
        # the channel is always rejected regardless of the randomness claimed.
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
