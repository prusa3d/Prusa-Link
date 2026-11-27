<script> // lan="ts">
    import { actions } from "./App.svelte"
    export let ap;
    export let connectionChange;
</script>

<form class="container p-0" action="/wifi/api/save" method="post" data-action="{actions.save}" on:submit|preventDefault={(e) => connectionChange(e, ap)}>
    {#if ap.ssid}
        <input type="hidden" name="ssid" value="{ap.ssid}">
    {:else}
        <div class="row pt-2 input-group">
            <div class="col-auto pr-0 input-group-prepend">
                <span class="input-group-text bg-dark text-white">SSID</span>
            </div>
            <input class="col form-control bg-dark text-white" type="text" name="ssid" value="">
        </div>
    {/if}
    <div class="row pt-2 input-group">
        <div class="col-auto pr-0 input-group-prepend">
            <span class="input-group-text bg-dark text-white">Password</span>
        </div>
        <input class="col form-control bg-dark text-white" type="password" name="password" value="">
    </div>
    <div class="row pt-2 pb-2 input-group">
        <div class="col-sm-auto pr-0">
            <input class="btn btn-outline-light full-width" type="submit" value="Connect">
        </div>
    </div>
</form>
