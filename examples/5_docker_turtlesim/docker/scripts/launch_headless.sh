#!/usr/bin/env bash
set -eo pipefail

# Source ROS2 environment
source /opt/ros/humble/setup.bash

echo "Starting ROS Bridge WebSocket server (headless mode)..."
echo "WebSocket server will be available at: ws://localhost:9090"
echo "This version runs without GUI for cross-platform compatibility"

# Launch rosbridge websocket server in the background
ros2 launch rosbridge_server rosbridge_websocket_launch.xml &

# Wait a moment for rosbridge to start
sleep 3

echo "Starting Turtlesim in headless mode..."
echo "Turtlesim will run without GUI display"
echo "You can still control it programmatically via ROS topics"

# Set Qt to use offscreen platform (no GUI)
export QT_QPA_PLATFORM=offscreen

# Launch turtlesim in headless mode
ros2 run turtlesim turtlesim_node &

sleep 2

echo ""
echo "🎉 Services started successfully!"
echo "📡 ROS Bridge WebSocket: ws://localhost:9090"
echo "🐢 Turtlesim topics available via ROS2"
echo "🛑 Use Ctrl+C to stop"
echo ""
echo "Available topics:"
echo "  - /turtle1/pose (turtle position)"
echo "  - /turtle1/cmd_vel (turtle movement commands)"
echo ""

# Trap signals to clean up processes
cleanup() {
    echo ""
    echo "🛑 Stopping turtlesim and rosbridge..."
    pkill -f turtlesim_node || true
    pkill -f rosbridge_server || true
    wait
    echo "✅ Cleanup complete"
}

trap cleanup SIGINT SIGTERM

# Keep container running and show status
while true; do
    sleep 5
    # Check if processes are still running
    if ! pgrep -f turtlesim_node > /dev/null; then
        echo "❌ Turtlesim process died, restarting..."
        ros2 run turtlesim turtlesim_node &
    fi
    if ! pgrep -f rosbridge_server > /dev/null; then
        echo "❌ ROS Bridge process died, restarting..."
        ros2 launch rosbridge_server rosbridge_websocket_launch.xml &
    fi
done
