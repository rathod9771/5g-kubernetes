#!/bin/bash

echo "================================"
echo "   5G RAN Selector Platform"
echo "================================"
echo ""
echo "Like choosing OS at boot time,"
echo "select your RAN deployment:"
echo ""
echo "1. srsRAN  (C-RAN - Centralized gNB)"
echo "2. OpenAirInterface (O-RAN - CU + DU split)"
echo "3. Status  (show current RAN)"
echo "4. Stop    (remove current RAN)"
echo ""
read -p "Enter choice [1-4]: " choice

case $choice in
  1)
    echo ""
    echo "Deploying srsRAN (C-RAN)..."
    # Remove OAI if running
    helm uninstall oai-cu -n free5gc 2>/dev/null || true
    helm uninstall oai-du -n free5gc 2>/dev/null || true
    # Deploy srsRAN
    helm install srsran ./helm/srsran -n free5gc
    echo "srsRAN deployed! Centralized gNB running."
    ;;
  2)
    echo ""
    echo "Deploying OpenAirInterface O-RAN (CU + DU)..."
    # Remove srsRAN if running
    helm uninstall srsran -n free5gc 2>/dev/null || true
    # Deploy OAI CU first then DU
    helm install oai-cu ./helm/oai/cu -n free5gc
    echo "Waiting for CU to be ready..."
    sleep 10
    helm install oai-du ./helm/oai/du -n free5gc
    echo "OAI O-RAN deployed! CU and DU running."
    ;;
  3)
    echo ""
    echo "Current RAN Status:"
    kubectl get pods -n free5gc | grep -E "srsran|oai"
    ;;
  4)
    echo ""
    echo "Stopping all RAN deployments..."
    helm uninstall srsran -n free5gc 2>/dev/null || true
    helm uninstall oai-cu -n free5gc 2>/dev/null || true
    helm uninstall oai-du -n free5gc 2>/dev/null || true
    echo "All RAN deployments stopped."
    ;;
  *)
    echo "Invalid choice!"
    ;;
esac
