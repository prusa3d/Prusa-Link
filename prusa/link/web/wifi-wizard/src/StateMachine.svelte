<script context="module">
    export const states = {
        disabled: 0,
        hotspotFlowStart: 1,
        disconnectedOnStart: 2,
        channelSwitch: 4,
        channelSwitchFailed: 5,
        ipsReported: 6,
        hotspotHandoff: 7,
        redirectImminent: 8,

        // Non hotspot states
        hostUnreachable: 100
    };
</script>


<script>
    import { onMount } from "svelte";

    const UPDATE_INTERVAL = 250;

    const PROBE_TIMEOUT = 7000;
    const CHANNEL_SWITCH_START_TIMEOUT = 1000;
    const CHANNEL_SWITCH_TIMEOUT = 15000;
    const HOTSPOT_HANDOFF_TIMEOUT = 4000;

    const ipAvailableSkipStates = [
        states.hotspotFlowStart,
        states.disconnectedOnStart,
        states.channelSwitch,
        states.channelSwitchFailed,
    ]

    const backToStartStates = [
        states.disconnectedOnStart,
        states.channelSwitch,
        states.channelSwitchFailed,
        states.ipsReported,
        states.hotspotHandoff,
    ]


    const ipReachableSkipStates = [
        states.hotspotFlowStart,
        states.disconnectedOnStart,
        states.channelSwitch,
        states.channelSwitchFailed,
        states.ipsReported,
        states.hotspotHandoff,
    ]

    let onHotspot = false;
    let hostReachable = true;
    let lastSeenHost = window.performance.now();
    let isConnecting = false;
    let availableIp = false;
    let reachableIp = false;

    export let state = states.disabled;

    export function newRawConnectionInfo(rawInfo) {
        if (!rawInfo["active_connections"] == undefined
            || !rawInfo["connection_details"])
        {
            return;
        }
        onHotspot = rawInfo["over_hotspot"];
        availableIp = rawInfo["connection_details"].length > 0;
        updateState()
    }

    export function newProbeResults(probeResults) {
        hostReachable = probeResults.some((probeResult) => {
            return probeResult.sameAsHost && probeResult.reachable;
        });
        if (hostReachable) {
            lastSeenHost = window.performance.now();
        }
        reachableIp = probeResults.some((probeResult) => {
            return !probeResult.sameAsHost && probeResult.reachable;
        });
        updateState()
    }

    export function newAps(aps) {
        isConnecting = aps.some((ap) => {
            return (ap.state == 1 || ap.state == 2)
        });
        console.log(isConnecting);
        console.log(aps);
    }

    function updateState() {
        let newState = state;
        // Start of the hotspot route
        if (state === states.disabled
            && onHotspot)
        {
            newState = states.hotspotFlowStart;
        }
        // Hotspot disconnected - ouch
        if (state === states.hotspotFlowStart
            && !isConnecting
            && !hostReachable
            && window.performance.now() - lastSeenHost > PROBE_TIMEOUT)
        {
            newState = states.disconnectedOnStart;
        }
        // We're connected, but seem to be back at the start
        if (backToStartStates.includes(state)
            && hostReachable
            && onHotspot
            && !isConnecting
            && !availableIp)
        {
            newState = states.hotspotFlowStart;
        }
        // Connecting, but host is unreachable. Probably switching channels
        if (state === states.hotspotFlowStart
            && isConnecting
            && !hostReachable
            && window.performance.now() - lastSeenHost > CHANNEL_SWITCH_START_TIMEOUT)
        {
            newState = states.channelSwitch;
        }
        // Taking too long, user probably needs to reconnect to the hotspot
        if (state === states.channelSwitch
            && isConnecting
            && !hostReachable
            && window.performance.now() - lastSeenHost > CHANNEL_SWITCH_TIMEOUT)
        {
            newState = states.channelSwitchFailed;
        }
        // Woohoo, we got the new interface ip
        if (ipAvailableSkipStates.includes(state)
            && onHotspot
            && availableIp)
        {
            newState = states.ipsReported;
        }
        // The host went away again, this should mean the hotspot turned off
        // Ask the user to connect to wherever they need to
        if (state === states.ipsReported
            && !hostReachable
            && window.performance.now() - lastSeenHost > HOTSPOT_HANDOFF_TIMEOUT)
        {
            newState = states.hotspotHandoff;
        }
        // Back on hotspot, still no redirect available
        if (state === states.hotspotHandoff
            && hostReachable
            && onHotspot
            && availableIp
            && !reachableIp)
        {
            newState = states.ipsReported;
        }
        // We have found an IP to redirect to, let's do that
        if (ipReachableSkipStates.includes(state)
            && reachableIp)
        {
            newState = states.redirectImminent;
        }

        // The user did not come from the hotspot
        if (state === states.disabled
            && !hostReachable
            && window.performance.now() - lastSeenHost > PROBE_TIMEOUT)
        {
            newState = states.hostUnreachable;
        }
        if (state === states.hostUnreachable
            && hostReachable)
        {
            newState = states.disabled;
        }
        state = newState;
        console.log(state);
    }


    onMount(() => {
        const updateInterval = setInterval(updateState, UPDATE_INTERVAL);

        return () => {
            clearInterval(updateInterval);
        }
    });
</script>
