import axios from "axios";
import {
    EP_LIST_DATA_LOADING_PLANS,
    EP_READ_DATA_LOADING_PLAN,
} from "../../constants";
import {displayError} from "./actions";


export const setChangeDlpMedicalFolderDataset = (use_dlp, state) => {
    return (dispatch) => {
        if(!use_dlp || state.dataLoadingPlan.selected_dlp_index === null) {
            dispatch({
                type: "RESET_MEDICAL_CHANGE_USED_DLP",
                payload: JSON.parse(JSON.stringify(state.medicalFolderDataset.default_modality_names))})
        } else {
            let dlp_id = state.dataLoadingPlan.existing_dlps.data[state.dataLoadingPlan.selected_dlp_index][1]

            dispatch({type:'SET_LOADING', payload: {status: true, text: "Reading Data Loading Plan content..."}})
            axios.post(EP_READ_DATA_LOADING_PLAN, {'dlp_id': dlp_id}).then(response => {
                dispatch({type:'SET_LOADING', payload: {status: false}})
                let data = response.data.result
                // default value if not set by DLP
                let dlp = {
                    use_custom_mod2fol: false,
                    current_modality_names: JSON.parse(JSON.stringify(state.medicalFolderDataset.default_modality_names)), // careful, not null
                    modalities_mapping: {}, // careful, not the initial null
                    mod2fol_mapping: {}, // careful, not the initial nul
                    has_all_mappings: false,
                    reference_csv: null,
                    ignore_reference_csv: false,
                }

                // DLP contains modality mapping
                if('map' in data) {
                    dlp['use_custom_mod2fol'] = true
                    dlp['mod2fol_mapping'] = JSON.parse(JSON.stringify(data.map))

                    // current_modality_names
                    for(const mod in data['map']) {
                        let found = false
                        for(const curmod of dlp['current_modality_names']) {
                            if(curmod['value'] === mod) {
                                found = true
                                break
                            }
                        }
                        if(!found) {
                            dlp['current_modality_names'].push({'label': mod, 'value': mod})
                        }
                    }

                    // modalities_mapping
                    for(const mod in data['map']) {
                        for(const folder of data['map'][mod]) {
                            if(state.medicalFolderDataset.modality_folders.includes(folder)) {
                                dlp['modalities_mapping'][folder] = mod
                            } else {
                            }
                            // ignore mappings that dont correspond to a folder in this dataset
                        }
                    }

                    // has_all_mappings
                    let has_all_mappings = true
                    for(const folder of state.medicalFolderDataset.modality_folders.values()) {
                        if(!dlp['modalities_mapping'][folder]) {
                            has_all_mappings = false
                            break
                        }
                    }
                    dlp['has_all_mappings'] = has_all_mappings
                }
                dispatch({type: "SET_MEDICAL_CHANGE_USED_DLP", payload: dlp})
                // dirty hack: need to force refresh of the ModalitiesToFolders
                if(dlp['use_custom_mod2fol'] === true) {
                    dispatch({type: 'SET_CUSTOMIZE_MOD2FOL', payload: false})
                    dispatch({type: 'SET_CUSTOMIZE_MOD2FOL', payload: true})
                }
            }).catch(error => {
                dispatch({type:'SET_LOADING', payload: {status: false}})
                dispatch(displayError(error, "Error while reading Data Loading Plan content."))
            })
        }
    }
}

export const setUsePreExistingDlp = (data) => {
    return (dispatch, getState) => {
        let state = getState()
        let use_dlp = data.target.value === 'true' ? true : false

        dispatch({type: "SET_USE_PRE_EXISTING_DLP", payload: use_dlp})
        if(data.target.value === 'true' && state.dataLoadingPlan.existing_dlps === null) {
            dispatch({type:'SET_LOADING', payload: {status: true, text: "Fetching existing Data Loading Plans..."}})
            axios.get(EP_LIST_DATA_LOADING_PLANS).then(response => {
                dispatch({type: "SET_EXISTING_DLPS", payload: response.data.result})
                dispatch({type:'SET_LOADING', payload: {status: false}})
                dispatch(setChangeDlpMedicalFolderDataset(use_dlp, state))
            }).catch(error => {
                dispatch({type:'SET_LOADING', payload: {status: false}})
                dispatch(displayError(error, "Error while fetching existing Data Loading Plans."))
            })
        } else {
            dispatch(setChangeDlpMedicalFolderDataset(use_dlp, state))
        }
    }
}

export const setDLPIndex = (event) => {
    return (dispatch, getState) => {
        dispatch({type: 'SET_DLP', payload: event.target.value})
        let state = getState() // needs to be done after setting DLP
        dispatch(setChangeDlpMedicalFolderDataset((event.target.value === '-1' ? false : true), state))
    }
}

export const setDLPDesc = (data) => {
    return (dispatch) => {
        dispatch({type: 'SET_DLP_NAME', payload: data})
    }
}

