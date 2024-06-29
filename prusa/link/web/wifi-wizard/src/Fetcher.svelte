<script>
    import { onMount } from "svelte"
    import { requestUrl, probe, fetcher } from './utils.js'

    const AP_FETCH_INTERVAL = 6000;
    const INFO_FETCH_INTERVAL = 2000;
    const PROBE_INTERVAL = 2500;

    export let aps = [];
    export let connectionDetails = [];
    export let rawConnectionInfo = {};
    export let probeDetails = [];
    export let probeResults = [];

    let isApSelected = false;
    let selectedAp = {};

    let probeTimeout = setTimeout(probeAll, PROBE_INTERVAL)

    export function selectAp(ap) {
        if (selectedAp.ssid == ap.ssid) {
            return;
        }
        isApSelected = true;
        selectedAp = ap;
    }

    /**
     * Sample Text
     * @param receivedAps
     */

    function processAps(receivedAps) {
        if (isApSelected) {
            let foundIndex = receivedAps.findIndex((ap) => ap.ssid == selectedAp.ssid);
            if (foundIndex != selectedAp.index) {
                if (foundIndex != -1) {
                    receivedAps.splice(foundIndex, 1);
                }
            receivedAps.splice(selectedAp.index, 0, selectedAp);
            }
        }

        for (let i = 0; i < receivedAps.length; i++) {
            let ap = receivedAps[i];
            ap.index = i;
        };

        aps = receivedAps;
    };

    function processConnectionInfo(info) {
        // TODO update ap status
        let newConnectionDetails = info["connection_details"]
        let protoProbes = [];

        newConnectionDetails.forEach(detail => {
            let activeConnection = info["active_connections"].find((ac) => ac.interface == detail.interface);
            if(activeConnection) {
                detail.ssid = activeConnection.ssid;
            }
            protoProbes.push({
                ip: detail.ip,
                detail: detail,
            });
        });
        if (protoProbes.find((e) => e.ip == window.location.hostname) === undefined) {
            protoProbes.push({ ip: window.location.hostname })
        }

        updateProbeDetails(protoProbes);
        connectionDetails = newConnectionDetails;
        rawConnectionInfo = info;
    };

    function updateProbeDetails(protoProbes) {
        let newProbeDetails = [];
        for (const protoProbe of protoProbes) {
            let detail = {
                ssid: protoProbe.detail?.ssid,
                ip: protoProbe.ip,
                url: requestUrl.replace(window.location.hostname, protoProbe.ip),
                sameAsHost: (window.location.hostname == protoProbe.ip),
                reachable: undefined,
            };
            newProbeDetails.push(detail);
        }
        probeDetails = newProbeDetails;
        clearTimeout(probeTimeout)
        probeAll();
    }

    async function fetchWifiList() {
        try {
            const response = await fetcher(requestUrl + "/wifi/api/ap_list")
            const data = await response.json();
            processAps(data.aps);
        } catch (error) {
            console.log(error);
        }
    }

    export async function fetchConnectionInfo() {
        try {
            const response = await fetcher(requestUrl + "/wifi/api/connection_info");
            const data = await response.json();
            processConnectionInfo(data);
        } catch (error) {
            console.log(error);
        };
    }


    async function probeAll() {
        let newProbeResults = [];
        for (const probeDetail of probeDetails) {
            probeDetail.reachable = await probe(probeDetail.url);
            newProbeResults.push(probeDetail);
        }
        probeResults = newProbeResults;
        probeTimeout = setTimeout(probeAll, PROBE_INTERVAL)
    }

    onMount(() => {
        const apInterval = setInterval(fetchWifiList, AP_FETCH_INTERVAL);
        const infoInterval = setInterval(fetchConnectionInfo, INFO_FETCH_INTERVAL);
        fetchWifiList();
        fetchConnectionInfo();

        return () => {
            clearInterval(apInterval);
            clearInterval(infoInterval);
        }
    });

</script>
