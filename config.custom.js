const webpackConfig = require("./webpack.config");

module.exports = (env, args) => {
    const config = {
        PRINTER_NAME: "Original Prusa MK3",
        PRINTER_TYPE: "fdm",

        WITH_SETTINGS: true,
        WITH_CONTROLS: true,
        WITH_LOGS: true,
        WITH_V1_API: true,
        ...env,
    };
    return webpackConfig(config, args);
}
