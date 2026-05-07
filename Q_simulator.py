import numpy as np
from qiskit import QuantumCircuit
from qiskit_aer import AerSimulator


class QuantumChannelSimulator:
    
    def __init__(self, shots=512, trust_threshold=0.04):
        self.simulator = AerSimulator()
        self.shots = shots
        self.trust_threshold = trust_threshold

    def quantum_measurement(self, theta):
        qc = QuantumCircuit(1, 1)

        qc.h(0)
        qc.rz(theta, 0)

        # Channel disturbance
        if np.random.rand() < 0.3:
            qc.z(0)

        qc.measure(0, 0)

        result = self.simulator.run(qc, shots=self.shots).result()
        counts = result.get_counts()

        bit_error = counts.get("1", 0) / self.shots
        return bit_error

    def compute_trust(self, bit_error, randomness):
        trust = (0.7 * randomness) + (0.3 * (1 - bit_error))
        return max(0.0, min(1.0, trust))

    def transmit(self, theta, randomness):
        bit_error = self.quantum_measurement(theta)
        trust = self.compute_trust(bit_error, randomness)

        if trust < self.trust_threshold:
            return {
                "status": "REJECTED",
                "bit_error": bit_error,
                "trust": trust
            }

        return {
            "status": "ACCEPTED",
            "bit_error": bit_error,
            "trust": trust
        }
