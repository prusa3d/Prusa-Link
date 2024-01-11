<script>
	import { states } from "./StateMachine.svelte"
	import { slide, fade } from "svelte/transition"
	export let state;
	export let availableIps;
	export let redirectIp;

	let dialog;

	const dialogShowStates = [
        states.disconnectedOnStart,
        states.channelSwitch,
        states.channelSwitchFailed,
        states.ipsReported,
        states.hotspotHandoff,
		states.redirectImminent,
		states.hostUnreachable,
	]

	function finishClosing() {
		dialog.classList.remove('hide');
		dialog.close();
		dialog.removeEventListener('animationend', finishClosing, false);
	}

	function hideDialog() {
		dialog.classList.add("hide");
		dialog.addEventListener('animationend', finishClosing, false);
	}

	$: if (dialog && dialogShowStates.includes(state)) dialog.showModal();
	$: if (dialog && dialog.open && !dialogShowStates.includes(state)) hideDialog();
</script>

<!-- svelte-ignore a11y-click-events-have-key-events a11y-no-noninteractive-element-interactions -->
<dialog
    class="border container p-4"
	bind:this={dialog}
>
	<!-- on:click|self={() => dialog.close()} -->

	{#key state}
		<!-- svelte-ignore a11y-no-static-element-interactions -->
		<div on:click|stopPropagation transition:slide={{ axis: "x" }}>
			<div class="row">
				<div class="col">
					<h2>
						{#if state == states.hostUnreachable}
							PrusaLink unreachable
						{:else if state == states.disconnectedOnStart }
							Hotspot connection lost
						{:else if state == states.channelSwitch }
							Please wait...
						{:else if state == states.channelSwitchFailed }
							Hotspot connection lost
						{:else if state == states.ipsReported }
							Connected, turning off hotspot
						{:else if state == states.hotspotHandoff }
							Connected
						{:else if state == states.redirectImminent }
							Success
						{/if}
					</h2>
				</div>
			</div>
			<div class="row">
				<div class="col">
					{#if state == states.hostUnreachable}
					<ul>
						<li>This webpage cannot connect to your PrusaLink</li>
						{#if availableIps.length > 0}
						<li>It seems to be available on another ip address though:
							<a href="{window.location.href.replace(window.location.hostname, availableIps[0])}">{availableIps[0]}</a>
						</li>
						{:else}
						<li>If you see PrusaLink hotspot in available Wi-Fi networks, connect to it again and go to <a href="http://prusalink.local">prusalink.local</a></li>
						{/if}
					</ul>
					{:else if state == states.disconnectedOnStart}
						<ul>
							<li>The connection has been unexpectedly lost here's some stuff to check</li>
							<li>Are there multiple PrusaLinks in setup mode at the same time?</li>
							<li>Is your PrusaLink device still on?</li>
							<li>Are you in range of the hotspot?</li>
						</ul>
					{:else if state == states.channelSwitch}
						<ul>
							<li>The connection process sometimes causes PrusaLink to be unresponsive for a bit.</li>
							<li>Everything is fine.jpg</li>
						</ul>
					{:else if state == states.channelSwitchFailed}
						<ul>
							<li>Your device has probably disconnected from the hotspot. Please connect to it again</li>
						</ul>
					{:else if state == states.ipsReported || state == states.hotspotHandoff}
						<ul>
							<li>To continue please connect back to your local network</li>
							<li>It's possible this will happen automatically</li>
						</ul>
					{:else if state == states.redirectImminent}
						<ul>
							<li>We'll redirect you to PrusaLinks new IP {redirectIp} shortly</li>
						</ul>
					{/if}
				</div>
			</div>
			<div class="row pt-3">
				{#if state == states.hostUnreachable}
					<div class="col" transition:fade>
						<button class="btn btn-outline-light" on:click={hideDialog}>Close</button>
					</div>
				{/if}
			</div>
		</div>
	{/key}
</dialog>

<style>
	dialog {
		max-width: 32em;
		border-radius: 0.2em;
        color: grey;
        background: black;
	}
	dialog::backdrop {
		background: rgba(0, 0, 0, 0.3);
	}
	dialog:focus:not(:focus-visible) {
    	outline: none;
	}
	:global(dialog.hide) {
		animation: zoom-out 0.5s ease-out !important;
	}
	dialog[open] {
		animation: zoom 0.5s cubic-bezier(0.34, 1.56, 0.64, 1);
	}
	@keyframes zoom {
		from {
			transform: scale(0.5);
		}
		to {
			transform: scale(1);
		}
	}
	@keyframes zoom-out {
		from {
			transform: scale(1);
		}
		to {
			transform: scale(0);
		}
	}
	:global(dialog.hide::backdrop) {
		animation: fade-out 0.3s ease-out !important;
	}
	dialog[open]::backdrop {
		animation: fade 0.3s ease-out;
	}
	@keyframes fade {
		from {
			opacity: 0;
		}
		to {
			opacity: 1;
		}
	}
	@keyframes fade-out {
		from {
			opacity: 1;
		}
		to {
			opacity: 0;
		}
	}
</style>
