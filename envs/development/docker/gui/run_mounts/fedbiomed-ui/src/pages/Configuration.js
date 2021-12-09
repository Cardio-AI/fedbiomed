import React from 'react';
import axios from 'axios';
import  {TableInfo} from "../components/Tables"

const Configuration = (props) => {

    const [config, setConfig] = React.useState(null)


    React.useEffect( () => {    
        get_node_config()
    }, [])

    /**
     * 
     * @param {string} dataset_id 
     */
    const get_node_config = () => {

        axios.post("/api/config/node-environ" , {})
                .then( res => {
                    if (res.status === 200){
                        setConfig(res.data.result) 
                    }else{
                        alert(res.data.message)
                    }
                })
                .catch( (error, res) => {
                    alert(error.response.data.message)
                })
    }

    const parse_config = () => {

        let obj = {}
        Object.keys(config).forEach( item => {
            obj[item] = {value : config[item]}
        })

        return obj
    }
    return (
        <React.Fragment>
            {
                config ? (
                    <TableInfo info={parse_config(config)}/>
                ) : null
            }
        </React.Fragment>
    );
}

export default Configuration;