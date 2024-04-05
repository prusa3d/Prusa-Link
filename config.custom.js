const webpackConfig = require("./webpack.config");

module.exports = (env, args) => {
    const config = {
        PRINTER_NAME: "Original Prusa i3",
        PRINTER_TYPE: "fdm",

        WITH_SETTINGS: true,
        WITH_CONTROLS: true,
        WITH_PROJECTS: true,
        WITH_LOGS: true,
        WITH_FONT: false,
        WITH_PRINT_BUTTON: true,
        WITH_V1_API: true,
        WITH_CAMERAS: true,
        WITH_DOWNLOAD_BUTTON: true,
        WITH_TELEMETRY_NOZZLE_DIAMETER: true,
        WITH_API_KEY_AUTH: false,
        WITH_API_KEY_SETTING: true,
        WITH_NAME_SORTING_ONLY: false,
        WITH_SYSTEM_UPDATES: true,

        WITH_SYSTEM_VERSION: true,
        WITH_PRINTER_SETTINGS: true,
        WITH_USER_SETTINGS: true,
        WITH_SERIAL: true,
        ...env,
    };
    return webpackConfig(config, args);
}
