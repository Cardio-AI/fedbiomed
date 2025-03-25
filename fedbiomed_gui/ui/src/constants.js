// BASE path
export const BASE_PATH                  = process.env.REACT_APP_BASE_PATH || ''
// login redirect path
export const LOGIN_REDIRECT_PATH        = `${BASE_PATH}/login`
// API Endpoints
export const API_ROOT                   = `${BASE_PATH}/api`
export const EP_DATASET_PREVIEW         = `${API_ROOT}/datasets/preview`
export const EP_DATASETS_LIST           = `${API_ROOT}/datasets/list`
export const EP_DATASET_REMOVE          = `${API_ROOT}/datasets/remove`
export const EP_REPOSITORY_LIST         = `${API_ROOT}/repository/list`
export const EP_DATASET_UPDATE          = `${API_ROOT}/datasets/update`
export const EP_DATASET_ADD             = `${API_ROOT}/datasets/add`
export const EP_DEFAULT_DATASET_ADD     = `${API_ROOT}/datasets/add-default-dataset`
export const EP_CONFIG_NODE_ENVIRON     = `${API_ROOT}/config/node-environ`
export const EP_LOAD_CSV_DATA           = `${API_ROOT}/datasets/get-csv-data`

// MedicalFolder Dataset Endpoints
export const EP_VALIDATE_MEDICAL_FOLDER_ROOT    = `${API_ROOT}/datasets/medical-folder-dataset/validate-root`
export const EP_VALIDATE_REFERENCE_COLUMN       = `${API_ROOT}/datasets/medical-folder-dataset/validate-reference-column`
export const EP_VALIDATE_SUBJECTS_ALL_MODALITIES = `${API_ROOT}/datasets/medical-folder-dataset/validate-all-modalities`
export const EP_ADD_MEDICAL_FOLDER_DATASET      = `${API_ROOT}/datasets/medical-folder-dataset/add`
export const EP_PREVIEW_MEDICAL_FOLDER_DATASET  = `${API_ROOT}/datasets/medical-folder-dataset/preview`
export const EP_DEFAULT_MODALITY_NAMES          = `${API_ROOT}/datasets/medical-folder-dataset/default-modalities`


// DataLoadingPlan Endpoints
export const EP_LIST_DATA_LOADING_PLANS         = `${API_ROOT}/datasets/list-dlps`
export const EP_READ_DATA_LOADING_PLAN          = `${API_ROOT}/datasets/read-dlp`
export const EP_ADD_DATA_LOADING_PLAN           = `${API_ROOT}/datasets/medical-folder-dataset/add-dlp`
export const EP_DELETE_DATA_LOADING_PLAN        = `${API_ROOT}/datasets/medical-folder-dataset/delete-dlp`

// Authentication endpoints
export const EP_LOGIN                   = `${API_ROOT}/auth/token/login`
export const EP_AUTH                    = `${API_ROOT}/token/auth`
export const EP_REFRESH                 = `${API_ROOT}/auth/token/refresh`
export const EP_LOGOUT                  = `${API_ROOT}/token/remove`
export const EP_REGISTER                = `${API_ROOT}/auth/register`
export const EP_UPDATE_PASSWORD         = `${API_ROOT}/update-password`

// Authentication actions
export const LOGIN                      = 'LOGIN'
export const REGISTER                   = 'REGISTER'

// Temporary endpoints
export const EP_PROTECTED               = `${API_ROOT}/protected`
export const EP_ADMIN                   = `${API_ROOT}/admin`

// Admin endpoints
export const EP_REQUESTS_LIST           = `${API_ROOT}/admin/requests/list`
export const EP_REQUEST_APPROVE         = `${API_ROOT}/admin/requests/approve`
export const EP_REQUEST_REJECT          = `${API_ROOT}/admin/requests/reject`
export const EP_LIST_USERS              = `${API_ROOT}/admin/users/list`
export const EP_REMOVE_USER             = `${API_ROOT}/admin/users/remove`
export const EP_CREATE_USER             = `${API_ROOT}/admin/users/create`
export const EP_RESET_USER_PASSWORD     = `${API_ROOT}/admin/users/reset-password`
export const EP_CHANGE_USER_ROLE        = `${API_ROOT}/admin/users/change-role`


//Models
export const EP_LIST_TRAINING_PLANS     = `${API_ROOT}/training-plan/list`
export const EP_APPROVE_TRAINING_PLAN   = `${API_ROOT}/training-plan/approve`
export const EP_REJECT_TRAINING_PLAN    = `${API_ROOT}/training-plan/reject`
export const EP_DELETE_TRAINING_PLAN    = `${API_ROOT}/training-plan/delete`
export const EP_PREVIEW_TRAINING_PLAN   = `${API_ROOT}/training-plan/preview`

// Messages
export const DATA_NOTFOUND = 'There is no data found for the dataset. It might be deleted'

// Form Handler
export const ADD_DATASET_ERROR_MESSAGES = {
    0 : { key: 'name', message: 'Dataset name is a required field'},
    1 : { key: 'type', message: 'Please select data type'},
    2 : { key: 'path', message: 'Please select data file'},
    3 : { key: 'tags', message: 'Please enter at least one tag for the dataset'},
    4 : { key: 'desc', message: 'Please enter a description for dataset'}
}

//Allowed file extensions for data loader
export const ALLOWED_EXTENSIONS = ['.csv', '.txt']

// role for authentication (User or admin)
export const ROLE = {1: 'Admin', 2: 'User'}

// account request status
export const NEW_REQUEST = 'NEW'
export const REJECTED_REQUEST = 'REJECTED'
