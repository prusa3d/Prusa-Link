<script context="module">
    export const actions = {
        save: "save",
        connect: "connect",
        disconnect: "disconnect",
        forget: "forget",
    }
</script>

<script>
    import { slide } from 'svelte/transition'
    import { handleFormData, requestUrl, changeHost, turnOffHotspot, probe } from './utils.js'
    import { states } from "./StateMachine.svelte"
    import ConnectForm from './ConnectForm.svelte'
    import Fetcher from './Fetcher.svelte'
    import Modal from './Modal.svelte'
    import StateMachine from './StateMachine.svelte'

    const REDIRECT_DELAY = 5000;

    const hotspotOffStates = [
        states.ipsReported,
        states.hotspotHandoff,
        states.redirectImminent,
    ]

    let modal;
    let fetcher;
    let stateMachine;

    let aps = [];
    let connectionDetails;
    let rawConnectionInfo;
    let probeDetails;
    let probeResults;

    let state;

    let selectedSSID;

    let availableIps;
    let redirectIp;
    let redirectTimeout;

    function selectAp(ap) {
        selectedSSID = ap.ssid;
        fetcher.selectAp(ap)
    }

    function backToWizard() {
        window.location.href = requestUrl + "/wizard";
    }

    function connectionChange(e, apToUpdate) {
        //TODO: update ap
        let action = e.target.getAttribute("data-action");
        let newAps = aps;
        let index = newAps.indexOf(apToUpdate)

        switch (action) {
            case actions.save:
            case actions.connect:
                if (apToUpdate?.state == 0) {
                    newAps[index].state = 1;
                }
                break;
            case actions.disconnect:
            case actions.forget:
                if (apToUpdate?.state == 2) {
                    newAps[index].state = 3;
                }
                break;
            default:
                console.log("data-action was not found action: " + action);
        }
        aps = newAps;
        handleFormData(e);
        setTimeout(fetcher.fetchConnectionInfo, 300);
    }

    async function autoRedirect(probeDetail) {
        let reachable = await probe(probeDetail.url);
        if (reachable) {
            changeHost(probeDetail.url)
        } else {
            redirectIp = undefined;
        }
    }

    async function redirectAttempt(receivedProbeResulte) {
        if (redirectIp !== undefined) {
            return;
        }
        if (state != states.redirectImminent) {
            return;
        }
        let potentialRedirectProbe;
        for (const probeResult of receivedProbeResulte) {
            if (probeResult.reachable && !probeResult.sameAsHost) {
                potentialRedirectProbe = probeResult;
                break;
            }
        }
        if (potentialRedirectProbe == undefined){
            return;
        }
        let reachable = await probe(potentialRedirectProbe.url)
        if (reachable) {
            redirectTimeout = setTimeout(() => {autoRedirect(potentialRedirectProbe)}, REDIRECT_DELAY);
            redirectIp = potentialRedirectProbe.ip
        }
    }

    function fillAvailableIps(receivedProbeResults) {
        let newAvailable = [];
        for (const probeResult of receivedProbeResults) {
            if (probeResult.ip && !probeResult.sameAsHost) {
                newAvailable.push(probeResult.ip);
            }
        }
        availableIps = newAvailable;
    }

    $: if (hotspotOffStates.includes(state) && rawConnectionInfo?.hotspot_on) turnOffHotspot();
    $: stateMachine?.newRawConnectionInfo(rawConnectionInfo);
    $: stateMachine?.newProbeResults(probeResults);
    $: stateMachine?.newAps(aps);
    $: if(probeResults) {
            redirectAttempt(probeResults);
            fillAvailableIps(probeResults);
        }

</script>


<Fetcher
    bind:this={fetcher}
    bind:aps
    bind:connectionDetails
    bind:rawConnectionInfo
    bind:probeDetails
    bind:probeResults
/>

<StateMachine
    bind:this={stateMachine}
    bind:state
/>


<h1 class="align-center">Wi-Fi Setup</h1>

{#if connectionDetails?.length}
    <h2 style="margin-bottom:0.3em;">Connections</h2>
    <div class="container mb-5">
        <div class="row border border-white pt-2 pb-2">
            <div class="col-auto" style="width: 125px">
                Interface
            </div>
            <div class="col-auto" style="width: 175px">
                IP address
            </div>
            <div class="col">
                SSID
            </div>
            <div class="col-auto" style="width: 175px"></div>
        </div>
        {#each connectionDetails as connectionDetail}
            {@const probeDetail = probeDetails.find((e) => e.ip == connectionDetail.ip)}
            {@const probeResult = probeResults.find((e) => e.ip == connectionDetail.ip)}
            <div class="row border border-white border-top-0 pt-2 pb-2">
                <div class="col-auto" style="width: 125px">
                    {connectionDetail.interface}
                </div>
                <div class="col-auto" style="width: 175px">
                    {connectionDetail.ip}
                </div>
                <div class="col">
                    {#if connectionDetail.ssid }
                        {connectionDetail.ssid}
                    {/if}
                </div>
                <div class="col-auto" style="width: 175px">
                    {#if probeDetail.sameAsHost}
                        <div class=" float-right">You are here</div>
                    {:else if probeResult == undefined}
                        <div class="float-right">
                            <div class="spinner-border text-light" role="status">
                                <span class="sr-only">Please wait...</span>
                            </div>
                        </div>
                    {:else if probeResult.reachable == true}
                        <button class="btn btn-outline-light float-right" on:click={() => changeHost(probeResult.url)}>Go there</button>
                    {:else}
                        <div class="float-right">Unreachable</div>
                    {/if}
                </div>
            </div>
        {/each}
    </div>
{:else}
    <h2>PrusaLink is not connected to any LAN network</h2>
{/if}

<h2 style="margin-bottom:0.3em;">Available networks</h2>
<div class="container mb-5">
    <div class="row border border-white pt-2 pb-2">
        <div class="col-auto" style="width: 60px;">
        </div>
        <div class="col">
                <span>SSID</span>
        </div>
        <div class="col-auto" style="width: 150px">
            State
        </div>
        <div class="col-auto" style="width: 125px">
                <span>Frequency</span>
        </div>
        <div class="col-auto" style="width: 150px">
        </div>
        <div class="w-100">
        </div>
    </div>
    {#each aps as ap (ap.ssid)}
        <!-- svelte-ignore a11y-no-static-element-interactions svelte-ignore a11y-click-events-have-key-events -->
        <div class="row border border-white border-top-0 pt-2 pb-2" on:click|stopPropagation={() => {selectAp(ap);}} transition:slide>
            <div class="col-auto" style="width: 60px;">
                <img height="25" src="img/{ap.strength_icon}" alt="{ap.strength_icon}">
            </div>
            <div class="col text-break" on:click={() => {selectAp(ap);}}>
                    <span>{ap.ssid ? ap.ssid : "[hidden]"}</span>
            </div>
            <div class="col-auto {ap.state == 2 ? 'text-white' : ''}" style="width: 150px">
                <span>
                    {#if ap.state >= 1 && ap.state <= 3}
                        {#if ap.state == 1}
                            Connecting
                        {:else if ap.state == 2}
                            Connected
                        {:else if ap.state == 3}
                            Disconnecting
                        {/if}
                    {:else if ap.saved}
                        Saved
                    {/if}
                </span>
            </div>
            <div class="col-auto" style="width:125px">
                    <span>{ap.frequency}</span>
            </div>
            <div class="col-auto" style="width: 150px">
                <button class="btn btn-outline-light float-right">Details</button>
            </div>
            <div class="w-100"></div>
            {#if selectedSSID === ap.ssid }
                <div class="container row" transition:slide>
                    <div class="col-auto" style="width: 60px;"></div>
                    {#if ap.saved}
                        <div class="col container">
                            <div class="row pt-2 pd-2 input-group">
                                {#if ap.state >= 1 && ap.state <= 3}
                                    <form class="col-auto" action="/wifi/api/disconnect" method="post" data-action="{actions.disconnect}" on:submit|preventDefault={(e) => {connectionChange(e, ap)}}>
                                        <input type="hidden" name="ssid" value="{ap.ssid}">
                                        <input class="btn btn-outline-light" type="submit" value="Disconnect">
                                    </form>
                                {:else}
                                    <form class="col-auto" action="/wifi/api/connect" method="post" data-action="{actions.connect}" on:submit|preventDefault={(e) => {connectionChange(e, ap)}}>
                                        <input type="hidden" name="ssid" value="{ap.ssid}">
                                        <input class="btn btn-outline-light" type="submit" value="Connect">
                                    </form>
                                {/if}
                                <form class="col-auto" action="/wifi/api/forget" method="post" data-action="{actions.forget}" on:submit|preventDefault={(e) => {connectionChange(e, ap)}}>
                                    <input type="hidden" name="ssid" value="{ap.ssid}">
                                    <input class="btn btn-outline-light" type="submit" value="Forget">
                                </form>
                            </div>
                        </div>
                    {:else}
                        <div class="col-md-5 col-lg-4 col container">
                            <ConnectForm {ap} {connectionChange}/>
                        </div>
                    {/if}
                    <div class="col-md"></div>
                </div>
            {/if}
        </div>
    {/each}
</div>

<h2 style="margin-bottom:0.2em;">Connect to another network</h2>
<div class="container p-0" transition:slide>
    <div class="row">
        <div class="col-lg-4 col">
            <ConnectForm ap={ {} } {connectionChange}/>
        </div>
        <div class="col-lg">
        </div>
    </div>
</div>

<div class="container navigation">
    <div class="row">
            <div class="col-sm-auto p-0">
                <button class="btn btn-outline-light full-width" on:click={backToWizard}>
                    Back to wizard <img src="img/arrow-left.svg" height="16" alt="back arrow"/>
                </button>
            </div>
    </div>
</div>

<Modal
    bind:this={modal}
    {availableIps}
    {redirectIp}
    {state}
/>
